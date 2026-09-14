#!/usr/bin/env python3
"""
Phase 2 Refiner: Filter repositories based on quality criteria.

Applies constraints to repository metadata:
  - stars >= 1
  - commit_count >= 1

Outputs:
  - Repositories meeting criteria: *-refiner.csv
  - Repositories failing criteria: *-criteria-not-met.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Allow reading large CSV fields
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2147483647)


# ============================================================================
# UTILITIES
# ============================================================================

def to_int(value: object, default: int = 0) -> int:
    """Convert value to integer safely."""
    try:
        return int(str(value).strip() or default)
    except (ValueError, TypeError, AttributeError):
        return default


def setup_logger(log_file: Path) -> logging.Logger:
    """Configure logging with file and console handlers."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger("phase-2-refiner")
    logger.setLevel(logging.DEBUG)
    
    # Remove existing handlers
    logger.handlers = []
    
    # File handler
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)
    
    logger.addHandler(fh)
    logger.addHandler(ch)
    
    return logger


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RefinerStats:
    """Track refinement statistics for a file."""
    file_name: str
    total_rows: int = 0
    rows_passed: int = 0
    rows_failed: int = 0
    failed_no_stars: int = 0
    failed_no_commits: int = 0
    failed_both: int = 0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class CheckpointData:
    """Track processing state for resumable processing."""
    file_index: int = 0
    total_files: int = 0
    files_processed: int = 0
    last_file: str = ""
    timestamp: str = ""
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


# ============================================================================
# MAIN REFINER CLASS
# ============================================================================

class Phase2Refiner:
    """Filter repositories based on quality criteria."""
    
    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        log_file: Path,
        checkpoint_file: Path,
        max_repos: Optional[int] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.input_path = input_path
        self.output_dir = output_dir
        self.log_file = log_file
        self.checkpoint_file = checkpoint_file
        self.max_repos = max_repos
        self.logger = logger or setup_logger(log_file)
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger.info("="*80)
        self.logger.info("Phase 2 Refiner initialized")
        self.logger.info(f"Input: {input_path}")
        self.logger.info(f"Output: {output_dir}")
        self.logger.info(f"Constraints: stars >= 1, commit_count >= 1")
        self.logger.info("="*80)
    
    def discover_input_files(self) -> List[Path]:
        """Find CSV files from input path (file or directory)."""
        if self.input_path.is_file():
            if self.input_path.suffix.lower() == '.csv':
                self.logger.info(f"Single file input: {self.input_path.name}")
                return [self.input_path]
            else:
                self.logger.error(f"Input file is not CSV: {self.input_path}")
                return []
        
        if self.input_path.is_dir():
            csv_files = sorted(self.input_path.glob("*.csv"))
            self.logger.info(f"Directory input: found {len(csv_files)} CSV files")
            return csv_files
        
        self.logger.error(f"Input path not found: {self.input_path}")
        return []
    
    def load_checkpoint(self) -> CheckpointData:
        """Load checkpoint from JSON file."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r') as f:
                    data = json.load(f)
                    self.logger.info(f"Checkpoint loaded: {data.get('last_file', 'N/A')}")
                    return CheckpointData(**data)
            except Exception as e:
                self.logger.warning(f"Failed to load checkpoint: {e}")
        
        return CheckpointData()
    
    def save_checkpoint(self, checkpoint: CheckpointData) -> None:
        """Save checkpoint to JSON file."""
        try:
            checkpoint.timestamp = datetime.now().isoformat()
            with open(self.checkpoint_file, 'w') as f:
                json.dump(checkpoint.to_dict(), f, indent=2)
            self.logger.debug(f"Checkpoint saved: {checkpoint.last_file}")
        except Exception as e:
            self.logger.error(f"Failed to save checkpoint: {e}")
    
    def read_csv(self, csv_file: Path) -> Tuple[List[Dict[str, str]], List[str]]:
        """Read CSV file and return rows with fieldnames."""
        try:
            with open(csv_file, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                fieldnames = reader.fieldnames or []
                self.logger.info(f"Read {len(rows)} rows from {csv_file.name}")
                return rows, fieldnames
        except Exception as e:
            self.logger.error(f"Failed to read {csv_file.name}: {e}")
            return [], []
    
    def write_csv(self, csv_file: Path, rows: List[Dict[str, str]], fieldnames: List[str]) -> None:
        """Write rows to CSV file."""
        try:
            with open(csv_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
                writer.writeheader()
                writer.writerows(rows)
            self.logger.info(f"Wrote {len(rows)} rows to {csv_file.name}")
        except Exception as e:
            self.logger.error(f"Failed to write {csv_file.name}: {e}")
    
    def apply_constraints(
        self, 
        rows: List[Dict[str, str]], 
        file_name: str
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], RefinerStats]:
        """Apply filtering constraints to rows."""
        
        stats = RefinerStats(file_name=file_name, total_rows=len(rows))
        passed_rows = []
        failed_rows = []
        
        # Use tqdm for row-level progress
        row_iter = rows
        if tqdm:
            row_iter = tqdm(
                rows,
                total=len(rows),
                desc=f"Filtering {file_name}",
                unit="row",
                leave=False
            )
        
        for row in row_iter:
            try:
                stars = to_int(row.get('stars', 0))
                commits = to_int(row.get('commit_count', 0))
                
                has_stars = stars >= 1
                has_commits = commits >= 1
                
                if has_stars and has_commits:
                    # Row passes all constraints
                    passed_rows.append(row)
                    stats.rows_passed += 1
                else:
                    # Row fails constraints
                    failed_rows.append(row)
                    stats.rows_failed += 1
                    
                    if not has_stars and not has_commits:
                        stats.failed_both += 1
                    elif not has_stars:
                        stats.failed_no_stars += 1
                    else:
                        stats.failed_no_commits += 1
                        
            except Exception as e:
                self.logger.error(f"Error processing row in {file_name}: {e}")
                failed_rows.append(row)
                stats.rows_failed += 1
        
        return passed_rows, failed_rows, stats
    
    def process_file(self, csv_file: Path) -> Optional[RefinerStats]:
        """Process a single CSV file."""
        self.logger.info(f"\nProcessing: {csv_file.name}")
        
        # Read CSV
        rows, fieldnames = self.read_csv(csv_file)
        if not rows:
            return None
        
        # Apply max_repos limit if specified
        if self.max_repos:
            rows = rows[:self.max_repos]
            self.logger.info(f"Limited to {len(rows)} rows (max_repos={self.max_repos})")
        
        # Apply constraints
        passed_rows, failed_rows, stats = self.apply_constraints(rows, csv_file.name)
        
        # Generate output file names
        stem = csv_file.stem  # filename without extension
        
        # Output passed rows with 'refiner' suffix
        if passed_rows:
            output_passed = self.output_dir / f"{stem}-refiner.csv"
            self.write_csv(output_passed, passed_rows, fieldnames)
        else:
            self.logger.warning(f"No rows passed criteria for {csv_file.name}")
        
        # Output failed rows with 'criteria-not-met' suffix
        if failed_rows:
            output_failed = self.output_dir / f"{stem}-criteria-not-met.csv"
            self.write_csv(output_failed, failed_rows, fieldnames)
        
        # Log statistics
        self.logger.info(
            f"Stats for {csv_file.name}: "
            f"Total={stats.total_rows}, "
            f"Passed={stats.rows_passed}, "
            f"Failed={stats.rows_failed} "
            f"(0-stars={stats.failed_no_stars}, "
            f"0-commits={stats.failed_no_commits}, "
            f"both={stats.failed_both})"
        )
        
        return stats
    
    def print_summary(self, all_stats: List[RefinerStats]) -> None:
        """Print summary report."""
        print("\n" + "="*140)
        print("PHASE 2 REFINER - SUMMARY REPORT")
        print("="*140)
        
        print(f"\n{'File Name':<50} {'Total':>8} {'Passed':>8} {'Failed':>8} "
              f"{'0-Stars':>8} {'0-Commits':>10} {'Both':>6}")
        print("-"*140)
        
        total_all = 0
        passed_all = 0
        failed_all = 0
        
        for stats in all_stats:
            print(
                f"{stats.file_name:<50} {stats.total_rows:>8} {stats.rows_passed:>8} "
                f"{stats.rows_failed:>8} {stats.failed_no_stars:>8} {stats.failed_no_commits:>10} {stats.failed_both:>6}"
            )
            total_all += stats.total_rows
            passed_all += stats.rows_passed
            failed_all += stats.rows_failed
        
        print("-"*140)
        print(
            f"{'TOTAL':<50} {total_all:>8} {passed_all:>8} {failed_all:>8}"
        )
        print("="*140)
        
        if total_all > 0:
            pass_rate = (passed_all / total_all) * 100
            print(f"\n✅ Overall Pass Rate: {pass_rate:.2f}% ({passed_all}/{total_all})")
            print(f"📊 Output files created in: {self.output_dir}")
            print("="*140 + "\n")
    
    def run(self) -> int:
        """Main execution flow."""
        try:
            # Discover input files
            input_files = self.discover_input_files()
            if not input_files:
                self.logger.error("No CSV files found")
                return 1
            
            # Load checkpoint
            checkpoint = self.load_checkpoint()
            start_idx = checkpoint.file_index if checkpoint.files_processed > 0 else 0
            
            # Process files
            all_stats = []
            file_iter = input_files[start_idx:]
            
            if tqdm:
                file_iter = tqdm(
                    file_iter,
                    total=len(input_files),
                    initial=start_idx,
                    desc="Files",
                    unit="file"
                )
            
            for idx, csv_file in enumerate(file_iter, start=start_idx):
                try:
                    stats = self.process_file(csv_file)
                    if stats:
                        all_stats.append(stats)
                    
                    # Update and save checkpoint
                    checkpoint.file_index = idx + 1
                    checkpoint.files_processed = len(all_stats)
                    checkpoint.last_file = csv_file.name
                    checkpoint.total_files = len(input_files)
                    self.save_checkpoint(checkpoint)
                    
                except Exception as e:
                    self.logger.error(f"Error processing {csv_file.name}: {e}")
                    continue
            
            # Print summary
            self.print_summary(all_stats)
            
            self.logger.info("Refinement completed successfully")
            return 0
            
        except KeyboardInterrupt:
            self.logger.info("Refinement interrupted by user")
            return 130
        except Exception as e:
            self.logger.error(f"Fatal error: {e}", exc_info=True)
            return 1


# ============================================================================
# CLI
# ============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Phase 2 Refiner: Filter repositories based on quality criteria"
    )
    parser.add_argument(
        "--input",
        help="Input CSV file or directory containing CSV files"
    )
    parser.add_argument(
        "--output",
        help="Output directory (default: phase-2-output-files)"
    )
    parser.add_argument(
        "--max-repos",
        type=int,
        help="Maximum repositories per file (optional)"
    )
    
    return parser.parse_args()


def detect_input_row_counts(input_path: Path) -> List[Tuple[str, int]]:
    """Detect number of rows in input CSV files."""
    files: List[Path]
    if input_path.is_file() and input_path.suffix.lower() == ".csv":
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(input_path.glob("*.csv"))
    else:
        return []

    counts: List[Tuple[str, int]] = []
    for file in files:
        try:
            with open(file, "r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = sum(1 for _ in reader) if reader.fieldnames else 0
                counts.append((file.name, rows))
        except Exception:
            counts.append((file.name, 0))
    return counts


def interactive_menu(default_input_dir: Path, default_output: Path) -> Tuple[Path, Path, Optional[int]]:
    """Interactive menu for configuration."""
    print("\n" + "="*80)
    print("Phase 2 Refiner - Interactive Menu")
    print("="*80)
    print("1. Use default input folder (phase-2-input-files)")
    print("2. Use a specific input CSV file")
    print("3. Use a specific input directory")
    print()

    choice = input("Select option [1/2/3]: ").strip() or "1"
    
    if choice == "2":
        input_path = Path(input("Enter CSV file path: ").strip()).expanduser().resolve()
    elif choice == "3":
        input_path = Path(input("Enter input directory path: ").strip()).expanduser().resolve()
    else:
        input_path = default_input_dir

    # Detect input rows
    counts = detect_input_row_counts(input_path)
    is_multi_file = len(counts) > 1
    
    if counts:
        print("\nDetected input files:")
        total_rows = 0
        for name, count in counts:
            print(f"  - {name}: {count} rows")
            total_rows += count
        print(f"  Total: {total_rows} rows")
    else:
        print(f"\nNo CSV files found in {input_path}")

    # Output directory configuration
    print()
    if is_multi_file:
        default_dir = default_input_dir.parent / "phase-2-output-files"
        output_text = input(f"Output directory [{default_dir}]: ").strip()
        output_path = Path(output_text).expanduser().resolve() if output_text else default_dir
    else:
        output_dir = default_input_dir.parent / "phase-2-output-files"
        output_text = input(f"Output directory [{output_dir}]: ").strip()
        output_path = Path(output_text).expanduser().resolve() if output_text else output_dir

    # Max repos configuration
    print()
    max_repos_text = input("Rows to process per file (blank for all rows): ").strip()
    max_repos = int(max_repos_text) if max_repos_text else None

    print("\n" + "="*80)
    print(f"Input:     {input_path}")
    print(f"Output:    {output_path}")
    print(f"Max repos: {max_repos if max_repos else 'No limit'}")
    print("="*80 + "\n")

    return input_path, output_path, max_repos


def main() -> int:
    """Entry point."""
    args = parse_args()
    default_input_dir = Path(__file__).resolve().parents[1] / "phase-2-input-files"
    default_output = default_input_dir.parent / "phase-2-output-files"

    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        output_path = Path(args.output).expanduser().resolve() if args.output else default_output
        max_repos = args.max_repos
    else:
        input_path, output_path, max_repos = interactive_menu(default_input_dir, default_output)

    # Ensure output directory exists
    output_path.mkdir(parents=True, exist_ok=True)

    log_dir = Path(__file__).parent / "logs"
    log_file = log_dir / "phase-2-refiner.log"
    checkpoint_file = Path(__file__).parent / "phase-2-refiner-checkpoint.json"
    
    # Initialize refiner
    logger = setup_logger(log_file)
    refiner = Phase2Refiner(
        input_path=input_path,
        output_dir=output_path,
        log_file=log_file,
        checkpoint_file=checkpoint_file,
        max_repos=max_repos,
        logger=logger
    )
    
    # Run refinement
    return refiner.run()


if __name__ == "__main__":
    sys.exit(main())