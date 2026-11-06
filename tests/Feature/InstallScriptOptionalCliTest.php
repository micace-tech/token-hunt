<?php

use Illuminate\Support\Facades\Http;

beforeEach(function () {
    config(['app.hook_namespace' => 'token_hunt']);
    Http::fake(['api.github.com/*' => Http::response(['message' => 'x'], 500)]);
});

test('a wheel download failure no longer aborts the install', function () {
    $script = $this->get('/install')->assertOk()->getContent();

    $msgPos = strpos($script, 'could not download the CLI');
    $branchEnd = strpos($script, "\nfi\n", $msgPos);
    $branch = substr($script, $msgPos, $branchEnd - $msgPos);

    expect($script)->toContain('SLAYER_CLI_OK=1');
    expect($branch)
        ->toContain('SLAYER_CLI_OK=0')
        ->not->toContain('exit 1');
});

test('an unreachable wheel URL is a soft failure too', function () {
    $script = $this->get('/install')->assertOk()->getContent();

    $msgPos = strpos($script, 'could not reach the wheel download URL');
    $branchEnd = strpos($script, "\nfi\n", $msgPos);

    expect(substr($script, $msgPos, $branchEnd - $msgPos))
        ->toContain('SLAYER_HTTP=000')
        ->not->toContain('exit 1');
});

test('the shared token-slayer shim and venv hooks are only written when the CLI installed', function () {
    $script = $this->get('/install')->assertOk()->getContent();

    $guard = 'if [ "$SLAYER_CLI_OK" = 1 ]; then';

    // Shim and its tok/slayer aliases: the guard opens before the shim is
    // written and closes before the always-on tokenhunt client.
    $guardPos = strpos($script, $guard);
    $shimPos = strpos($script, 'cat > "$HOME/.local/bin/token-slayer"');
    $aliasPos = strpos($script, 'ln -sf "$HOME/.local/bin/token-slayer" "$HOME/.local/bin/tok"');
    $clientPos = strpos($script, 'TOKENHUNT_URL=');

    expect($guardPos)->toBeInt()->toBeLessThan($shimPos);
    expect($aliasPos)->toBeLessThan($clientPos);

    // The two hooks that run the venv's python are inside the second guard.
    $secondGuardPos = strpos($script, $guard, $clientPos);
    $usageRefreshPos = strpos($script, 'hook usage-refresh');
    $sessionTrackPos = strpos($script, 'hook session-track-start');
    $trackEchoPos = strpos($script, 'installed Claude Code session-tracking hook');

    expect($secondGuardPos)->toBeInt()->toBeLessThan($usageRefreshPos);
    expect($sessionTrackPos)->toBeLessThan($trackEchoPos);
    expect(substr($script, $trackEchoPos, 160))->toContain("\nfi\n");
});

test('the tokenhunt client is installed under a namespaced name that never replaces another hub', function () {
    $script = $this->get('/install')->assertOk()->getContent();

    expect($script)
        ->toContain('/tokenhunt.py')
        ->toContain('.config/token_hunt/tokenhunt.py')
        ->toContain('tokenhunt-token_hunt')
        ->toContain('[ -e "$HOME/.local/bin/tokenhunt" ] || ln -s');
});

test('the rendered install script is still valid POSIX shell', function () {
    $script = $this->get('/install')->assertOk()->getContent();

    $path = tempnam(sys_get_temp_dir(), 'slayer-install-').'.sh';
    file_put_contents($path, $script);

    exec('sh -n '.escapeshellarg($path).' 2>&1', $output, $exit);
    @unlink($path);

    expect($exit)->toBe(0, "sh -n reported syntax errors:\n".implode("\n", $output));
});

test('the tokenhunt client file is published and speaks the provisioning API', function () {
    $client = file_get_contents(public_path('tokenhunt.py'));

    expect($client)
        ->toContain('/api/provisioned')
        ->toContain('/api/provisioned/confirm')
        ->toContain('Authorization');
});

test('install.ps1 keeps going without the wheel and leaves other hubs\' shims alone', function () {
    $script = $this->get(route('install-script-ps1'))->assertOk()->getContent();

    // soft failure on a non-401 wheel error
    $elsePos = strpos($script, 'could not download the CLI (server said');
    expect($script)->toContain('$CliOk = $true');
    expect(substr($script, $elsePos - 40, 420))
        ->toContain('Write-Warning')
        ->toContain('$CliOk = $false')
        ->not->toContain('throw');

    // tok/slayer/token-slayer shims, venv hooks and detect-base are gated
    expect($script)
        ->toContain("if (\$CliOk) {\n  foreach (\$n in 'tok','slayer','token-slayer')")
        ->toContain("if (\$CliOk) {\n# Register a second, always-on Stop hook")
        ->toContain('if ($CliOk -and (Test-Path $VenvPy))');

    // the namespaced client is always installed
    expect($script)
        ->toContain('/tokenhunt.py')
        ->toContain('tokenhunt-$Ns.cmd');
});

test('the 401 branch still aborts the PowerShell install', function () {
    $script = $this->get(route('install-script-ps1'))->assertOk()->getContent();

    $pos = strpos($script, 'your token is missing or no longer valid');

    expect(substr($script, $pos - 40, 60))->toContain('throw');
});

test('installer messages use the configured app name instead of token-slayer', function () {
    config(['app.name' => 'Token Hunt']);

    $sh = $this->get('/install')->assertOk()->getContent();
    $ps1 = $this->get(route('install-script-ps1'))->assertOk()->getContent();

    expect($sh)
        ->toContain('Open your Token Hunt profile page')
        ->not->toContain('Open your token-slayer profile page');
    expect($ps1)
        ->toContain("\$AppName       = 'Token Hunt'")
        ->toContain('Open your $AppName profile page')
        ->not->toContain('Open your token-slayer profile page');
});
