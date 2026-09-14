#!/usr/bin/env python3
"""
Phase 1 – Link Standardization & Data Integrity Script
Processes raw repository exports into standardized, clean "Seed Files".
Supports multiple input files with checkpointing for resumable execution.
"""

import csv
import re
import sys
import json
import signal
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict
from urllib.parse import urlparse

try:
    from tqdm import tqdm
except ImportError:
    print("⚠️  tqdm not installed. Installing...")
    import os
    os.system(f"{sys.executable} -m pip install tqdm -q")
    from tqdm import tqdm

try:
    import requests
except ImportError:
    print("⚠️  requests not installed. Installing...")
    import os
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

# Color codes for tqdm progress bars
COLORS = {
    0: "cyan",
    1: "green", 
    2: "yellow",
    3: "magenta",
    4: "blue",
    5: "red"
}


# Configure logging
def setup_logging(log_file: str = "phase1_execution.log"):
    """Setup logging configuration"""
    log_path = Path(log_file)
    
    # Create logger
    logger = logging.getLogger('Phase1')
    logger.setLevel(logging.DEBUG)
    
    # Create handlers
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


logger = setup_logging()


class CheckpointManager:
    """Manages checkpoints for resumable execution with JSON format"""
    
    def __init__(self, checkpoint_file: str = ".phase1_checkpoint.json"):
        self.checkpoint_file = Path(checkpoint_file)
        self.data = self._load()
    
    def _load(self) -> Dict:
        """Load checkpoint if exists"""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load checkpoint: {e}. Starting fresh.")
        
        return {
            'processed_files': {},
            'processed_urls': {},
            'failed_urls': {},
            'duplicates': {},
            'timestamp': datetime.now().isoformat()
        }
    
    def save(self):
        """Save checkpoint to JSON"""
        self.data['timestamp'] = datetime.now().isoformat()
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Checkpoint saved to {self.checkpoint_file}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
    
    def is_file_processed(self, file_path: str) -> bool:
        """Check if file already processed"""
        return file_path in self.data['processed_files']
    
    def get_processed_urls(self, file_path: str) -> Set[str]:
        """Get already processed URLs for a file"""
        return set(self.data['processed_urls'].get(file_path, []))
    
    def add_processed_url(self, file_path: str, url: str):
        """Add processed URL"""
        if file_path not in self.data['processed_urls']:
            self.data['processed_urls'][file_path] = []
        if url not in self.data['processed_urls'][file_path]:
            self.data['processed_urls'][file_path].append(url)
    
    def add_duplicate(self, file_path: str, url: str, reason: str = "duplicate"):
        """Track duplicate URLs"""
        if file_path not in self.data['duplicates']:
            self.data['duplicates'][file_path] = []
        self.data['duplicates'][file_path].append({
            'url': url,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        })
    
    def mark_file_complete(self, file_path: str):
        """Mark file as completely processed"""
        self.data['processed_files'][file_path] = {
            'status': 'completed',
            'timestamp': datetime.now().isoformat()
        }
    
    def add_failed_url(self, file_path: str, url: str, reason: str):
        """Add failed URL"""
        if file_path not in self.data['failed_urls']:
            self.data['failed_urls'][file_path] = []
        self.data['failed_urls'][file_path].append({
            'url': url,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        })


class Phase1LinkStandardizer:
    """Standardizes and cleans GitHub repository links with checkpoint support"""
    
    def __init__(self, base_path: str = ".", output_dir: str = None):
        self.base_path = Path(base_path)
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.base_path / "phase-1-output"
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.github_url_regex = re.compile(r'github\.com\/([^\/]+)\/([^\/\s?#]+)')
        self.stats = defaultdict(lambda: {
            'total_raw': 0,
            'total_processed': 0,
            'discarded': 0,
            'unique_clean': 0,
            'errors': 0
        })
        self.checkpoint = CheckpointManager()
        self.url_cache = {}  # Cache for URL validation results
        self._setup_signal_handlers()
        
        logger.info(f"Phase1LinkStandardizer initialized")
        logger.info(f"Output directory: {self.output_dir.absolute()}")
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signals"""
        signal_name = {signal.SIGINT: "SIGINT (Ctrl+C)", signal.SIGTERM: "SIGTERM"}.get(signum, f"Signal {signum}")
        print(f"\n\n⚠️  Received {signal_name}")
        print("💾 Saving checkpoint...")
        self.checkpoint.save()
        print("✓ Checkpoint saved. You can resume later.")
        sys.exit(130)
    
    def normalize_url(self, url: str) -> str:
        """
        Normalize URL: lowercase, remove trailing slashes, .git, branch info
        """
        if not url:
            return ""
        
        # Convert to lowercase
        url = url.lower()
        
        # Remove trailing slashes
        url = url.rstrip('/')
        
        # Remove .git suffix
        if url.endswith('.git'):
            url = url[:-4]
        
        # Remove branch information (/tree/main, /tree/master, etc.)
        url = re.sub(r'/tree/[^/]+$', '', url)
        
        # Remove query parameters and fragments
        url = url.split('?')[0].split('#')[0]
        
        return url.strip()
    
    def extract_and_reconstruct(self, url: str) -> Tuple[bool, str]:
        """
        Extract owner/repo using regex and reconstruct URL
        Returns: (is_valid, reconstructed_url)
        Regex pattern: github\.com\/([^\/]+)\/([^\/\s?#]+)
        """
        # First normalize
        normalized = self.normalize_url(url)
        
        if not normalized:
            return False, ""
        
        # Try to match the regex pattern
        match = self.github_url_regex.search(normalized)
        
        if not match:
            return False, ""
        
        owner, repo = match.groups()
        
        # Validate: ensure we have exactly owner/repo (2 segments)
        if not owner or not repo:
            return False, ""
        
        # Remove any remaining slashes or special characters from segments
        owner = owner.split('/')[0]  # Take only first part if multiple slashes exist
        repo = repo.split('/')[0]
        
        # Reconstruct the URL
        reconstructed = f"https://github.com/{owner}/{repo}"
        
        return True, reconstructed
    
    def validate_url_status(self, url: str, timeout: int = 5) -> Tuple[bool, int, str]:
        """
        Validate URL by checking HTTP status
        Returns: (is_valid, status_code, status_message)
        """
        # Check cache first
        if url in self.url_cache:
            return self.url_cache[url]
        
        try:
            response = requests.head(url, timeout=timeout, allow_redirects=True)
            status = response.status_code
            is_valid = 200 <= status < 400
            message = f"HTTP {status}"
            
            result = (is_valid, status, message)
            self.url_cache[url] = result
            
            logger.debug(f"URL validation: {url} -> {message}")
            return result
            
        except requests.Timeout:
            result = (False, 0, "Timeout")
            logger.warning(f"URL validation timeout: {url}")
            return result
        except requests.ConnectionError:
            result = (False, 0, "Connection error")
            logger.warning(f"URL connection error: {url}")
            return result
        except Exception as e:
            result = (False, 0, f"Error: {str(e)[:50]}")
            logger.warning(f"URL validation error: {url} - {str(e)}")
            return result
    
    def _count_rows(self, input_file: Path) -> int:
        """Count total rows in CSV file"""
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                return sum(1 for _ in f) - 1  # Subtract header
        except:
            return 0
    
    def process_file(self, input_file: Path, url_column: str, file_index: int) -> Tuple[int, int, int, Set[str], Set[str], List[str]]:
        """
        Process a single CSV file with checkpoint support
        Returns: (total_raw, discarded, unique_clean, clean_urls_set, duplicate_urls_set, errors)
        """
        file_key = str(input_file)
        total_raw = 0
        discarded = 0
        clean_urls = set()
        duplicate_urls = set()
        errors = []
        
        logger.info(f"Starting to process file: {input_file.name}")
        
        try:
            # Check if already processed
            if self.checkpoint.is_file_processed(file_key):
                logger.info(f"File already processed. Loading from checkpoint: {input_file.name}")
                print(f"   ⏭️  File already processed. Loading from checkpoint...")
                clean_urls = self.checkpoint.get_processed_urls(file_key)
                return 0, 0, len(clean_urls), clean_urls, set(), []
            
            # Get previously processed URLs from checkpoint
            previous_urls = self.checkpoint.get_processed_urls(file_key)
            clean_urls.update(previous_urls)
            
            with open(input_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                if not reader.fieldnames or url_column not in reader.fieldnames:
                    error_msg = f"Column '{url_column}' not found in {input_file.name}"
                    print(f"❌ {error_msg}")
                    print(f"   Available columns: {reader.fieldnames}")
                    logger.error(error_msg)
                    errors.append(error_msg)
                    return 0, 0, 0, set(), set(), errors
                
                logger.info(f"URL column detected: '{url_column}'")
                
                # Count total rows
                total_rows = self._count_rows(input_file)
                
                # Get color for this file
                color = COLORS.get(file_index % len(COLORS), "white")
                
                # Create progress bar with tqdm
                rows = list(reader)
                for row in tqdm(rows, desc=f"   Processing {input_file.name}", 
                               colour=color, unit="URL", leave=True):
                    try:
                        raw_url = row.get(url_column, "").strip()
                        
                        if not raw_url:
                            discarded += 1
                            logger.debug(f"Empty URL skipped in {input_file.name}")
                            continue
                        
                        total_raw += 1
                        logger.debug(f"Processing URL: {raw_url}")
                        
                        # Extract and reconstruct
                        is_valid, clean_url = self.extract_and_reconstruct(raw_url)
                        
                        if is_valid:
                            # Check if it's a duplicate within this file or globally
                            if clean_url in clean_urls:
                                duplicate_urls.add(clean_url)
                                self.checkpoint.add_duplicate(file_key, clean_url, "duplicate_detected")
                                logger.debug(f"Duplicate detected: {clean_url}")
                            else:
                                clean_urls.add(clean_url)
                                self.checkpoint.add_processed_url(file_key, clean_url)
                                logger.debug(f"Valid URL processed: {clean_url}")
                        else:
                            discarded += 1
                            reason = "Invalid URL format or missing owner/repo"
                            self.checkpoint.add_failed_url(file_key, raw_url, reason)
                            logger.warning(f"Invalid URL: {raw_url} - {reason}")
                            errors.append(f"Malformed URL: {raw_url[:80]}")
                    
                    except Exception as row_error:
                        error_msg = f"Row processing error: {str(row_error)}"
                        errors.append(error_msg)
                        discarded += 1
                        logger.error(f"Exception processing row in {input_file.name}: {error_msg}")
                
                # Save checkpoint after processing
                self.checkpoint.save()
                logger.info(f"Checkpoint saved after processing {input_file.name}")
        
        except FileNotFoundError:
            error_msg = f"File not found: {input_file}"
            print(f"❌ {error_msg}")
            logger.error(error_msg)
            errors.append(error_msg)
            return 0, 0, 0, set(), set(), errors
        
        except Exception as e:
            error_msg = f"Error processing {input_file.name}: {str(e)}"
            print(f"❌ {error_msg}")
            logger.error(error_msg)
            errors.append(error_msg)
            return 0, 0, 0, set(), set(), errors
        
        return total_raw, discarded, len(clean_urls), clean_urls, duplicate_urls, errors
    
    def save_cleaned_file(self, output_file: Path, clean_urls: Set[str], errors: List[str], input_filename: str = None, validate_urls: bool = False) -> bool:
        """Save cleaned URLs to CSV file"""
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                
                # Write header with validation info
                writer.writerow(['repo_url', 'validation_status', 'processed_timestamp'])
                
                # Write each URL with validation status
                for url in sorted(clean_urls):  # Sort for consistency
                    if validate_urls:
                        # Optional: Perform HTTP validation (slower)
                        is_valid, status, message = self.validate_url_status(url)
                        validation_status = f"HTTP {status}" if status > 0 else "OK"
                    else:
                        # Fast path: Just mark as OK (format is already validated)
                        validation_status = "OK"
                    
                    writer.writerow([
                        url,
                        validation_status,
                        datetime.now().isoformat()
                    ])
            
            logger.info(f"Output file saved: {output_file.name} ({len(clean_urls)} URLs)")
            return True
        except Exception as e:
            error_msg = f"Error saving {output_file.name}: {str(e)}"
            print(f"❌ {error_msg}")
            logger.error(error_msg)
            errors.append(error_msg)
            return False
    
    def save_duplicates_file(self, input_filename: str, duplicate_urls: Set[str], errors: List[str]) -> bool:
        """Save duplicate URLs to a separate CSV file"""
        if not duplicate_urls:
            return True
        
        try:
            # Create duplicates filename with input filename
            stem = Path(input_filename).stem
            duplicates_filename = f"{stem}-duplicates.csv"
            duplicates_file = self.output_dir / duplicates_filename
            
            with open(duplicates_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f, quoting=csv.QUOTE_ALL)
                
                # Write header
                writer.writerow(['duplicate_url', 'detected_timestamp', 'source_file'])
                
                # Write each duplicate URL
                for url in sorted(duplicate_urls):
                    writer.writerow([
                        url,
                        datetime.now().isoformat(),
                        input_filename
                    ])
            
            logger.info(f"Duplicates file saved: {duplicates_filename} ({len(duplicate_urls)} URLs)")
            return True
        except Exception as e:
            error_msg = f"Error saving duplicates file for {input_filename}: {str(e)}"
            print(f"❌ {error_msg}")
            logger.error(error_msg)
            errors.append(error_msg)
            return False
    
    def process_all(self, input_files: List[str]):
        """Process all input files"""
        print("\n" + "="*80)
        print("PHASE 1: LINK STANDARDIZATION & DATA INTEGRITY")
        print("="*80 + "\n")
        
        if not input_files:
            print("❌ No input files provided!")
            print("Usage: python3 standardise_links.py file1.csv [file2.csv] [file3.csv]")
            return False
        
        all_clean_urls = set()
        all_errors = []
        
        for file_index, input_path_str in enumerate(input_files):
            input_file = Path(input_path_str)
            
            # Generate output filename with "-output" suffix
            output_filename = f"{input_file.stem}-output{input_file.suffix}"
            output_file = self.output_dir / output_filename
            
            print(f"📄 [{file_index + 1}/{len(input_files)}] Processing: {input_file.name}")
            
            # Check if file exists
            if not input_file.exists():
                error_msg = f"File not found: {input_file}"
                print(f"   ❌ {error_msg}")
                all_errors.append(error_msg)
                continue
            
            # Detect URL column dynamically
            url_column = self._detect_url_column(input_file)
            if not url_column:
                error_msg = f"Could not detect URL column in {input_file.name}"
                print(f"   ❌ {error_msg}")
                all_errors.append(error_msg)
                continue
            
            print(f"   Column detected: '{url_column}'")
            print(f"   Output file: {output_file.name}")
            
            # Process file
            total_raw, discarded, unique_clean, clean_urls, duplicate_urls, file_errors = self.process_file(
                input_file,
                url_column,
                file_index
            )
            
            all_errors.extend(file_errors)
            
            # Save cleaned file
            if clean_urls:
                save_success = self.save_cleaned_file(output_file, clean_urls, all_errors, input_file.name, validate_urls=False)
                if save_success:
                    print(f"   ✅ Saved: {output_filename} ({len(clean_urls)} URLs)")
                    logger.info(f"Saved output file: {output_filename} with {len(clean_urls)} URLs")
                else:
                    print(f"   ❌ Failed to save: {output_filename}")
                    logger.error(f"Failed to save: {output_filename}")
            else:
                print(f"   ⚠️  No valid URLs found")
                logger.warning(f"No valid URLs found in {input_file.name}")
            
            # Save duplicates file if duplicates found
            if duplicate_urls:
                dup_success = self.save_duplicates_file(input_file.name, duplicate_urls, all_errors)
                if dup_success:
                    print(f"   ⚠️  Duplicates file saved ({len(duplicate_urls)} duplicates)")
                    logger.info(f"Saved duplicates file with {len(duplicate_urls)} URLs from {input_file.name}")
                else:
                    print(f"   ❌ Failed to save duplicates file")
                    logger.error(f"Failed to save duplicates file for {input_file.name}")
            
            # Track statistics
            self.stats[input_file.name]['total_raw'] = total_raw
            self.stats[input_file.name]['discarded'] = discarded
            self.stats[input_file.name]['unique_clean'] = unique_clean
            self.stats[input_file.name]['errors'] = len(file_errors)
            
            # Track for aggregate
            all_clean_urls.update(clean_urls)
            
            print(f"   Total raw: {total_raw}")
            print(f"   Discarded: {discarded}")
            print(f"   Unique clean: {unique_clean}")
            if file_errors:
                print(f"   Errors: {len(file_errors)}")
            print()
        
        # Save final checkpoint
        self.checkpoint.mark_file_complete(str(input_files))
        self.checkpoint.save()
        
        # Print summary
        self.print_summary(all_clean_urls, all_errors)
        
        return True
    
    def _detect_url_column(self, input_file: Path) -> Optional[str]:
        """Auto-detect URL column in CSV"""
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                candidates = ['repo_url', 'github_url', 'url', 'link', 'repository_url']
                
                if not reader.fieldnames:
                    return None
                
                fields_lower = [f.lower() for f in reader.fieldnames]
                
                for candidate in candidates:
                    if candidate in fields_lower:
                        # Return original field name with proper case
                        idx = fields_lower.index(candidate)
                        return reader.fieldnames[idx]
                
                # If no candidate found, return first field
                return reader.fieldnames[0] if reader.fieldnames else None
        
        except Exception:
            return None
    
    def print_summary(self, all_urls: Set[str], all_errors: List[str]):
        """Print execution summary"""
        print("\n" + "="*80)
        print("SUMMARY REPORT")
        print("="*80 + "\n")
        
        total_raw_all = 0
        total_discarded_all = 0
        total_unique_all = 0
        total_errors_all = 0
        
        print("📊 PER-FILE STATISTICS:\n")
        
        for filename, stats in self.stats.items():
            print(f"  {filename}:")
            print(f"    • Total raw links: {stats['total_raw']}")
            print(f"    • Discarded (malformed): {stats['discarded']}")
            print(f"    • Unique clean links: {stats['unique_clean']}")
            print(f"    • Errors: {stats['errors']}")
            
            total_raw_all += stats['total_raw']
            total_discarded_all += stats['discarded']
            total_unique_all += stats['unique_clean']
            total_errors_all += stats['errors']
            print()
        
        print("="*80)
        print("📈 AGGREGATE STATISTICS:\n")
        print(f"  Total raw links across all files: {total_raw_all}")
        print(f"  Total discarded links: {total_discarded_all}")
        print(f"  Total unique clean links (deduplicated): {len(all_urls)}")
        print(f"  Deduplication reduction: {total_unique_all - len(all_urls)} URLs")
        if total_raw_all > 0:
            print(f"  Data quality rate: {((total_raw_all - total_discarded_all) / total_raw_all * 100):.2f}%")
        print(f"  Total errors encountered: {total_errors_all}")
        
        if all_errors:
            print(f"\n⚠️  ERRORS ({len(all_errors)}):")
            for i, error in enumerate(all_errors[:10], 1):  # Show first 10 errors
                print(f"    {i}. {error}")
            if len(all_errors) > 10:
                print(f"    ... and {len(all_errors) - 10} more errors")
        
        print("\n" + "="*80)
        print("✅ PHASE 1 COMPLETE")
        print("="*80)
        print(f"\n📁 Output files saved to: {self.output_dir.absolute()}\n")


def main():
    """Main entry point with interactive file/folder input"""
    print("\n" + "="*80)
    print("PHASE 1: LINK STANDARDIZATION & DATA INTEGRITY")
    print("="*80 + "\n")
    
    # Get input path from user (file or folder)
    print("📁 Enter CSV file or folder path:")
    print("   • Provide an absolute or relative path")
    print("   • If folder: all *.csv files will be processed")
    print("   • If file: only that file will be processed\n")
    
    input_path = None
    while not input_path:
        user_input = input("   Path: ").strip()
        
        if not user_input:
            print("   ❌ Please enter a valid path.")
            continue
        
        path = Path(user_input)
        
        if not path.exists():
            print(f"   ❌ Path does not exist: {path}")
            continue
        
        input_path = path
    
    # Determine input files based on path type
    input_files = []
    
    if input_path.is_file():
        # Single file
        if input_path.suffix.lower() == '.csv':
            input_files = [str(input_path)]
            print(f"\n✅ File selected: {input_path.name}")
        else:
            print(f"   ❌ File is not a CSV: {input_path.name}")
            sys.exit(1)
    
    elif input_path.is_dir():
        # Folder - find all CSV files
        csv_files = sorted(input_path.glob('*.csv'))
        if not csv_files:
            print(f"   ❌ No CSV files found in: {input_path}")
            sys.exit(1)
        
        input_files = [str(f) for f in csv_files]
        print(f"\n✅ Folder selected: {input_path.name}")
        print(f"   Found {len(input_files)} CSV file(s):")
        for f in csv_files:
            print(f"      • {f.name}")
    else:
        print(f"   ❌ Invalid path: {input_path}")
        sys.exit(1)
    
    # Determine output directory
    # If input is in phase-1-input-files, output to phase-1-output-files
    # Otherwise, create phase-1-output in the input's parent directory
    if input_path.is_file():
        input_parent = input_path.parent
    else:
        input_parent = input_path
    
    if input_parent.name == 'phase-1-input-files':
        # Use the sibling output directory
        output_dir = input_parent.parent / 'phase-1-output-files'
    else:
        # Use phase-1-output in the input directory
        output_dir = input_parent / 'phase-1-output'
    
    print(f"   Output directory: {output_dir}\n")
    
    # Ask about checkpoint
    print("💾 Checkpoint Settings:")
    checkpoint_input = input("   Use default checkpoint (.phase1_checkpoint.json)? [Y/n]: ").strip().lower()
    
    if checkpoint_input == 'n':
        custom_checkpoint = input("   Enter checkpoint file name: ").strip()
        if not custom_checkpoint:
            print("   ❌ Invalid checkpoint name. Using default.")
            checkpoint_file = '.phase1_checkpoint.json'
        else:
            checkpoint_file = custom_checkpoint
    else:
        checkpoint_file = '.phase1_checkpoint.json'
    
    print(f"   Using: {checkpoint_file}\n")
    
    # Create standardizer with explicit output directory
    standardizer = Phase1LinkStandardizer(base_path=".", output_dir=str(output_dir))
    standardizer.checkpoint = CheckpointManager(checkpoint_file=checkpoint_file)
    
    # Process all files
    try:
        success = standardizer.process_all(input_files)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Process interrupted by user")
        print("💾 Checkpoint saved. Run the script again to resume.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

