{{-- resources/views/guide.blade.php --}}
@extends('layouts.app')

@php
    $accountTasks = [
        [
            'q' => 'See all your accounts',
            'cmd' => 'tok  (or tok status)',
            'desc' => "Prints the account pool as a table — every slot, auth state, and live 5h/7d usage, with the active one marked. This is what bare <code>tok</code> and <code>tok status</code> both show by default; <code>tok list</code> shows the same table from the local cache instead of re-fetching usage (faster, slightly stale).",
            'example' => 'tok',
        ],
        [
            'q' => 'Browse and switch interactively',
            'cmd' => 'tok tui',
            'desc' => "Opens the full-screen interactive TUI to browse every slot and switch without typing a target. Bare <code>tok</code> only prints a static table — this is the one that lets you act on it directly.",
            'example' => 'tok tui',
        ],
        [
            'q' => 'Add another account',
            'cmd' => 'tok add NAME  [--login]',
            'desc' => "Without <code>--login</code>, snapshots whichever account Claude Code is already logged into as a new slot under the name you give it — log in first (<code>claude</code>, then <code>/login</code>), then run this. With <code>--login</code>, it drives a fresh sign-in itself instead, so you can add a different account without disturbing the one you're currently on.",
            'example' => "tok add work\n# or, to sign into a different account without switching first:\ntok add personal --login",
        ],
        [
            'q' => 'Switch which account is active',
            'cmd' => 'tok switch TARGET',
            'desc' => "Switches which Claude account is active on this machine. TARGET can be a slot's index, the name you gave it with <code>add</code>, an alias, or its email — whichever's fastest to type.",
            'example' => "tok switch work\n# or, by index:\ntok switch 2",
            'tip' => "Not sure of the name or index? Run <code>tok</code> (or <code>tok status</code>) to see the table with every slot's index — or run <code>tok tui</code> to browse and switch in one step.",
        ],
        [
            'q' => 'Force-switch when the normal switch is stuck',
            'cmd' => 'tok force-switch TARGET',
            'desc' => 'Switches to TARGET (slot/alias/email) while bypassing the usual rotation-capture step — for when a normal <code>switch</code> is refusing to cooperate.',
            'example' => 'tok force-switch work',
        ],
        [
            'q' => 'See which account is currently active',
            'cmd' => 'tok current',
            'desc' => "Prints just the active slot's name and email/org — a quick check without the full table.",
            'example' => 'tok current',
        ],
        [
            'q' => 'Give a slot a short alias',
            'cmd' => 'tok alias TARGET [ALIAS]',
            'desc' => 'Sets ALIAS as the short name for account TARGET (index/name/email). Omit ALIAS to clear whatever alias is already set.',
            'example' => 'tok alias work w',
        ],
        [
            'q' => 'Remove an account slot',
            'cmd' => 'tok remove TARGET',
            'desc' => 'Removes the slot TARGET (index/name/alias/email). If you have other accounts left, one of them becomes active automatically.',
            'example' => 'tok remove old-account',
        ],
        [
            'q' => 'Pull an org-provisioned account',
            'cmd' => 'tok setup',
            'desc' => 'For accounts an admin already provisioned for you — fetches them, drops any that got revoked, and configures Claude Code in one step, no manual login needed.',
            'example' => 'tok setup',
        ],
        [
            'q' => 'Register your current login as a slot',
            'cmd' => 'tok detect-base',
            'desc' => "Registers whatever Claude login is already active on this machine as a tracked slot. Idempotent — does nothing if no login is active or that account is already tracked. Mostly useful right after install, before you've run <code>add</code> for anything.",
            'example' => 'tok detect-base',
        ],
        [
            'q' => 'See recent account swaps',
            'cmd' => 'tok history',
            'desc' => 'Shows the log of recent automatic and manual account swaps, most recent first.',
            'example' => "tok history\n# last 5 only:\ntok history -n 5",
        ],
        [
            'q' => 'See which Claude Code sessions are running',
            'cmd' => 'tok sessions',
            'desc' => 'Lists running Claude Code sessions on this machine with their derived status.',
            'example' => 'tok sessions',
        ],
        [
            'q' => 'Reconcile accounts after a manual change',
            'cmd' => 'tok sync',
            'desc' => "Reconciles every stored account identity against Claude's own login state and the hook attribution files — run it if something looks out of sync after editing files by hand. <code>--dry-run</code> shows the plan without writing anything.",
            'example' => "tok sync --dry-run\ntok sync",
        ],
        [
            'q' => 'Update the switcher itself',
            'cmd' => 'tok update',
            'desc' => 'Re-runs the served install script to pick up the latest version of the switcher.',
            'example' => 'tok update',
        ],
        [
            'q' => 'Inspect or refresh account usage',
            'cmd' => 'tok usage show|refresh',
            'desc' => "<code>show</code> prints the on-disk usage cache (5h/7d, per seat) as JSON, no network call. <code>refresh</code> fetches fresh usage for every account first, then shows the same list — this is what the main table's numbers come from.",
            'example' => "tok usage show\ntok usage refresh",
        ],
        [
            'q' => 'Remove the switcher entirely',
            'cmd' => 'tok uninstall',
            'desc' => "Restores your original Claude login, removes the switcher's venv and shim, and (unless <code>--keep-accounts</code> is given) clears stored account slots, switch state, and history. Does not touch the token-tracking hook itself (<code>send-hook.sh</code>, hook token, <code>custom.sh</code>) — that's removed separately.",
            'example' => "tok uninstall\n# keep the account slots, just remove the switcher:\ntok uninstall --keep-accounts",
        ],
    ];

    $customTasks = [
        [
            'q' => 'Show something custom instead of the default label',
            'cmd' => "~/.config/{$namespace}/custom.sh",
            'desc' => "The <code>~/.config/{$namespace}</code> folder already exists after install — you just add this file yourself. It's sourced right before every event is sent, with <code>\$BODY</code> (the JSON payload) in scope to edit with <code>\$JQ</code> (the pinned, checksum-verified jq the installer manages — not necessarily a system <code>jq</code> on PATH). It survives every install and update since installers never touch or overwrite it. Set <code>custom_activity</code> in <code>\$BODY</code> and the server shows it verbatim instead of its own default label.",
            'example' => "if [ -x \"\$JQ\" ]; then\n  BODY=\$(printf '%s' \"\$BODY\" | \"\$JQ\" -c '\n    if (.hook_event_name // \"\") == \"UserPromptSubmit\" then\n      .custom_activity = \"New prompt\"\n    elif (.hook_event_name // \"\") == \"PreToolUse\" then\n      .custom_activity = ({\n        \"Bash\": \"Execute\",\n        \"Task\": (\"Agent: \" + (.tool_input.description // \"subagent\"))\n      }[.tool_name] // .tool_name)\n    else . end' 2>/dev/null || printf '%s' \"\$BODY\")\nfi",
        ],
    ];

    // Without a configured slayer-cli release channel there is no `tok` CLI;
    // the installer ships the lightweight `tokenhunt` client instead.
    $cliAvailable = app(\App\Services\GitHub\GitHubClient::class)->isConfigured();
    $installUrl = url('/install');

    $tokenhuntTasks = [
        [
            'q' => 'See all your accounts',
            'cmd' => 'tokenhunt list  [--json]',
            'desc' => "Prints every account slot with its plan, state and the 5h/7d usage from the last refresh. <code>--json</code> prints the same data for scripts (tokens are never included). Slots are stored in <code>~/.config/{$namespace}/accounts.json</code>.",
            'example' => "tokenhunt list\ntokenhunt list --json",
            'tip' => "Full name: <code>tokenhunt-{$namespace}</code>. The short <code>tokenhunt</code> link is only created when that name is free, so it never replaces another hub's command.",
        ],
        [
            'q' => 'See which account is currently active',
            'cmd' => 'tokenhunt current',
            'desc' => 'Prints the active slot\'s name, email and plan.',
            'example' => 'tokenhunt current',
        ],
        [
            'q' => 'Save the account you are logged into',
            'cmd' => 'tokenhunt add [NAME]',
            'desc' => 'Log in with Claude Code first (<code>claude</code>, then <code>/login</code>), then run this to keep that login as a slot. NAME defaults to the part of the email before the @.',
            'example' => "tokenhunt add work",
        ],
        [
            'q' => 'Switch which account is active',
            'cmd' => 'tokenhunt switch TARGET',
            'desc' => 'Makes a slot the live Claude Code login. TARGET is an index, name, alias or email. The login you are leaving is saved first (including tokens Claude Code refreshed on its own), and the account label in <code>~/.claude.json</code> follows the switch so <code>/status</code> shows the right account. Restart Claude Code afterwards.',
            'example' => "tokenhunt switch work\n# or by index:\ntokenhunt switch 2",
            'tip' => 'If the current login cannot be identified, the switch is refused so it cannot be lost. Run <code>tokenhunt add NAME</code> first, or pass <code>--force</code>.',
        ],
        [
            'q' => 'Give a slot a short alias',
            'cmd' => 'tokenhunt alias TARGET [ALIAS]',
            'desc' => 'Sets ALIAS as a short name for the slot. Omit ALIAS to clear it.',
            'example' => 'tokenhunt alias work w',
        ],
        [
            'q' => 'Remove an account slot',
            'cmd' => 'tokenhunt remove TARGET',
            'desc' => 'Deletes a stored slot. The active slot cannot be removed: switch to another one first.',
            'example' => 'tokenhunt remove old-account',
        ],
        [
            'q' => 'Check or refresh account usage',
            'cmd' => 'tokenhunt usage show|refresh',
            'desc' => '<code>show</code> prints the cached 5h/7d usage as JSON with no network call. <code>refresh</code> fetches fresh usage for every slot, then shows the table. Stale tokens of inactive slots are renewed; the active slot is never renewed here because Claude Code owns its token (open Claude Code once if it reports an expired token).',
            'example' => "tokenhunt usage show\ntokenhunt usage refresh",
        ],
        [
            'q' => 'Reconcile accounts after a manual change',
            'cmd' => 'tokenhunt sync  [--dry-run]',
            'desc' => 'Stores the live login\'s current tokens in its slot and repairs the account label in <code>~/.claude.json</code> if it points at a different account. <code>--dry-run</code> shows what would change.',
            'example' => "tokenhunt sync --dry-run\ntokenhunt sync",
        ],
        [
            'q' => 'Pull an org-provisioned account',
            'cmd' => 'tokenhunt setup',
            'desc' => 'For an account an admin already provisioned for this machine: fetches the grant, saves your current login as a slot, installs the new account as the active one, then confirms with the server. No manual login needed. Switch back any time with <code>tokenhunt switch</code>. Restart Claude Code afterwards.',
            'example' => "tokenhunt setup --dry-run\ntokenhunt setup",
            'tip' => '<code>--dry-run</code> previews without changing anything; <code>--account EMAIL</code> picks one when several were provisioned; <code>--yes</code> skips the prompt.',
        ],
        [
            'q' => 'Check this machine is set up',
            'cmd' => 'tokenhunt status',
            'desc' => 'Shows the namespace, the server, whether the hook token and script are present, where Claude Code keeps its login and how many slots exist. It changes nothing and needs no network.',
            'example' => 'tokenhunt status',
        ],
        [
            'q' => 'Update the hooks and the client',
            'cmd' => "curl -fsSL {$installUrl} | sh",
            'desc' => 'Re-run the install command from the Setup page. It is idempotent and also refreshes <code>tokenhunt</code>. If your hook token was regenerated, use the command shown on the Setup page, which carries the new token.',
            'example' => "curl -fsSL {$installUrl} | sh",
        ],
    ];

    if (! $cliAvailable) {
        $accountTasks = $tokenhuntTasks;
    }

    $topics = [
        'accounts' => ['label' => 'Accounts', 'tasks' => $accountTasks],
        'custom' => ['label' => 'Custom hook display', 'tasks' => $customTasks],
    ];
@endphp

@section('content')
    @include('partials.account-nav', ['active' => 'guide'])

    <div class="max-w-3xl mx-auto p-8" x-data="{ topic: 'accounts', openTask: null }">
        <h1 class="text-2xl font-bold text-gray-900 mb-1">Command reference</h1>
        <p class="text-sm text-gray-500 mb-6">Pick what you want to do — the command shows up below. @if ($cliAvailable)Everything here also has the full form <code class="text-gray-700">token-slayer</code>, in case <code class="text-gray-700">tok</code> isn't on PATH yet.@else This server ships the lightweight <code class="text-gray-700">tokenhunt</code> client instead of the <code class="text-gray-700">tok</code> switcher.@endif</p>

        <div class="flex gap-6">
            <nav class="w-44 flex-shrink-0 space-y-1">
                @foreach ($topics as $key => $t)
                    <button
                        type="button"
                        @click="topic = '{{ $key }}'; openTask = null"
                        class="cursor-pointer block w-full text-left px-3 py-2.5 rounded-lg text-sm font-semibold transition"
                        :class="topic === '{{ $key }}' ? 'bg-orange-50 text-orange-600' : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'"
                    >{{ $t['label'] }}</button>
                @endforeach
            </nav>

            <div class="flex-1 min-w-0">
                <div x-show="topic === 'accounts'" x-cloak>
                    <div class="bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded-lg px-3 py-2 mb-4">
                        <span class="font-medium">macOS:</span> the first {!! $cliAvailable ? '<code>tok switch</code> (or <code>setup</code>)' : '<code>tokenhunt setup</code>' !!} pops a Keychain prompt asking for your login password/Touch ID — choose
                        <em>Always Allow</em> to skip repeat prompts. On a brand-new Mac, <code>python3</code> may be
                        an Xcode stub that pops its own "Install Command Line Developer Tools?" dialog — check with
                        <code>xcode-select -p</code> first (empty output → run <code>xcode-select --install</code>).
                    </div>
                    @foreach ($accountTasks as $i => $task)
                        @include('partials.guide.task', ['task' => $task, 'topicKey' => 'accounts', 'i' => $i])
                    @endforeach
                    <p class="text-xs text-gray-400 mt-3">
                        @if ($cliAvailable)
                            <code>tok --help</code> or <code>tok &lt;command&gt; --help</code> for full details on any of these.
                        @else
                            <code>tokenhunt --help</code> or <code>tokenhunt setup --help</code> for every option.
                        @endif
                    </p>
                </div>

                <div x-show="topic === 'custom'" x-cloak>
                    <p class="text-sm text-gray-600 mb-4">By default the charging bubble shows only a privacy-safe tool name — no commands, file paths, or prompts.</p>
                    @foreach ($customTasks as $i => $task)
                        @include('partials.guide.task', ['task' => $task, 'topicKey' => 'custom', 'i' => $i])
                    @endforeach

                    <p class="text-sm text-gray-600 mt-4 mb-2">These fields are available only inside <code>custom.sh</code>, on your own machine. <code>tool_input</code> is never sent to the server &mdash; the hook filters the payload down to usage and attribution fields after <code>custom.sh</code> runs, so you can read anything locally and only the label you build leaves the machine.</p>

                    <div class="overflow-x-auto mt-4">
                        <table class="w-full text-left text-sm">
                            <thead class="text-xs text-gray-500 uppercase">
                                <tr><th class="py-2">Provider</th><th>Example <code>tool_name</code></th><th>Useful <code>tool_input</code> fields</th></tr>
                            </thead>
                            <tbody class="divide-y divide-gray-100">
                                <tr>
                                    <td class="py-2 font-medium align-top">Claude Code</td>
                                    <td class="align-top text-gray-600"><code>Bash</code>, <code>Read</code>, <code>Edit</code>, <code>Write</code>, <code>Grep</code>, <code>WebFetch</code>, <code>Task</code></td>
                                    <td class="align-top text-gray-600"><code>command</code>, <code>file_path</code>, <code>pattern</code>, <code>url</code>, <code>description</code></td>
                                </tr>
                                <tr>
                                    <td class="py-2 font-medium align-top">Any provider · MCP tools</td>
                                    <td class="align-top text-gray-600"><code>mcp__&lt;server&gt;__&lt;tool&gt;</code>, e.g. <code>mcp__jira__jira_search_issues</code></td>
                                    <td class="align-top text-gray-600">shape varies per tool; the server name (segment after the first <code>__</code>) is the most reliable thing to key off</td>
                                </tr>
                                <tr>
                                    <td class="py-2 font-medium align-top">Antigravity</td>
                                    <td class="align-top text-gray-600"><code>run_command</code>, <code>read_file</code>, <code>write_file</code>, <code>grep_search</code></td>
                                    <td class="align-top text-gray-600"><code>CommandLine</code>, <code>AbsolutePath</code>, <code>TargetFile</code>, <code>Query</code></td>
                                </tr>
                                <tr>
                                    <td class="py-2 font-medium align-top">Codex CLI</td>
                                    <td class="align-top text-gray-400" colspan="2">no per-tool events today — only session start/stop are wired, so there's nothing to key off yet</td>
                                </tr>
                                <tr>
                                    <td class="py-2 font-medium align-top">claude.ai / Cowork</td>
                                    <td class="align-top text-gray-400" colspan="2">no tool events — these only ever report a token count on session end</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>
@endsection
