# gherkin_model_dump.rb -- dump a .feature file's parsed model as JSON.
#
# Uses cuke_modeler, which wraps the OFFICIAL Cucumber Gherkin parser. That
# matters: the question this answers is not "does our own parser think the file
# is unchanged?" but "does CUCUMBER think so?" -- and only Cucumber's parser can
# answer that.
#
# What is emitted is exactly what determines runtime behaviour:
#   * the step TEXT, in order  -- the string a step definition is matched against
#   * scenario and feature names
#   * tags
#
# What is deliberately NOT emitted: indentation, blank lines, trailing
# whitespace. Those are what a form-preserving repair is allowed to change, so
# including them would make every repair look like a semantic change.
#
#   ruby gherkin_model_dump.rb FILE...    -> one JSON object per line
require 'cuke_modeler'
require 'json'

ARGV.each do |path|
  begin
    file = CukeModeler::FeatureFile.new(path)
    feature = file.feature
    if feature.nil?
      puts JSON.generate({'path' => path, 'error' => 'no feature'})
      next
    end
    tests = feature.tests.map do |t|
      { 'name'  => t.name.to_s.strip,
        'tags'  => (t.tags || []).map { |x| x.name.to_s },
        'steps' => t.steps.map { |s| s.text.to_s.strip } }
    end
    background = feature.background ?
      feature.background.steps.map { |s| s.text.to_s.strip } : []
    puts JSON.generate({
      'path'       => path,
      'feature'    => feature.name.to_s.strip,
      'tags'       => (feature.tags || []).map { |x| x.name.to_s },
      'background' => background,
      'tests'      => tests
    })
  rescue => e
    # A parse failure is itself a result: if a file parsed before a repair and
    # not after, the repair broke it, which is the most serious outcome there is.
    puts JSON.generate({'path' => path, 'error' => "#{e.class}: #{e.message}"})
  end
end
