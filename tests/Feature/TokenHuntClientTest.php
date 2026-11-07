<?php

test('the tokenhunt client passes its end-to-end suite against fake hub and Anthropic servers', function () {
    exec('command -v python3', $found, $missing);
    if ($missing !== 0) {
        $this->markTestSkipped('python3 is not available');
    }

    exec(
        'cd '.escapeshellarg(base_path()).' && python3 -I -m unittest discover -s tests/tokenhunt 2>&1',
        $output,
        $exit,
    );

    expect($exit)->toBe(0, implode("\n", $output));
});
