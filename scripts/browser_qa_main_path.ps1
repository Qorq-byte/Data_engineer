param([string]$BrowseExe = (Join-Path $env:USERPROFILE ".agents/skills/gstack/browse/dist/browse.exe"))
# Stage 48 main-path check: connection scope -> query -> result/error -> retry ->
# history. Complements scripts/browser_qa.ps1 (which covers cancel, mobile dock,
# modal keyboard and the offline-schema / invitation cases).
#
# Windows PowerShell 5.1 drops embedded double quotes when handing arguments to a
# native exe and a GBK console decodes browse's UTF-8 output into mojibake, so run
# this with pwsh 7.3+:  pwsh -NoProfile -File scripts/browser_qa_main_path.ps1
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:BROWSE_PARENT_PID = '0'
$browser = $BrowseExe
if (!(Test-Path -LiteralPath $browser)) { throw "Supply -BrowseExe with the installed gstack /browse binary" }

function B {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $result = & $browser @args 2>&1 } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw ($result -join "`n") }
    $result
}
function AssertText($expected) {
    $actual = (B text) -join "`n"
    if (!$actual.Contains($expected)) { throw "Expected text missing: $expected`n$actual" }
    "PASS text: $expected"
}
function SendQuery($text) {
    B fill 'textarea[aria-label="数据问题"]' $text
    B click 'button[aria-label="发送查询"]'
}

B goto http://127.0.0.1:8018/workspace
B eval .verify_tmp_scripts/browser_auth.js
B goto http://127.0.0.1:8018/workspace

# 1. connection scope: the selector must list the registered fixture and accept a
#    concrete db_id instead of only "all databases".
B js 'const s=[...document.querySelectorAll("select")].find(x=>[...x.options].some(o=>o.value==="qa-fixture")); if(!s) throw new Error("query-scope select missing"); const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set; set.call(s,"qa-fixture"); s.dispatchEvent(new Event("change",{bubbles:true})); "PASS query scope offers qa-fixture"'
Start-Sleep -Milliseconds 300
B js 'const s=[...document.querySelectorAll("select")].find(x=>[...x.options].some(o=>o.value==="qa-fixture")); if(s.value!=="qa-fixture") throw new Error("scope not applied: "+s.value); ({scope:s.value, options:[...s.options].map(o=>o.value)})'
Write-Output 'PASS query scope applied to a single database'

# 2. result path with the scope pinned to one database
SendQuery normal_case
Start-Sleep -Seconds 2
AssertText '查询执行完成 · 3 行'
B js 'const t=document.getElementById("root").textContent; if(!t.includes("qa-fixture")) throw new Error("scope label missing after run"); "PASS scope label survives the run"'

# 3. error path
B click 'button[title="新建对话"]'
SendQuery error_case
Start-Sleep -Seconds 1
AssertText '查询失败'
B js 'const t=document.getElementById("root").textContent; if(t.includes("查询执行完成")) throw new Error("error case reported as success"); "PASS error case is not reported as success"'

# 4. retry: after a failure the same input must be submittable again and succeed
SendQuery normal_case
Start-Sleep -Seconds 2
AssertText '查询执行完成 · 3 行'
B js 'const t=document.getElementById("root").textContent; const ok=t.split("查询执行完成").length-1; if(ok<1) throw new Error("retry produced no success message"); "PASS retry after failure succeeds"'

# 5. history: panel lists the run and reopens it
B click 'button[title="历史查询"]'
Start-Sleep -Milliseconds 400
B js 'const h=[...document.querySelectorAll("div.max-h-64 button")]; if(h.length<1) throw new Error("history panel empty after "+document.querySelectorAll("div.max-h-64 button").length+" items"); "PASS history panel lists runs: "+h.length'
B js 'const h=[...document.querySelectorAll("div.max-h-64 button")]; h[0].click(); "PASS clicked newest history entry"'
Start-Sleep -Seconds 1
AssertText '查询执行完成'
B js 'const open=document.querySelectorAll("div.max-h-64").length>0; "PASS history panel state after selection: "+(open?"still open":"closed by the UI")'

# 6. mobile: reopen the panel and check it does not overflow the viewport
B click 'button[title="历史查询"]'
Start-Sleep -Milliseconds 400
B js 'if(!document.querySelectorAll("div.max-h-64").length) throw new Error("history panel did not reopen"); "PASS history panel reopens"'
B viewport 375x812
B js 'if(document.documentElement.scrollWidth>innerWidth) throw new Error("Mobile overflow with history open: "+document.documentElement.scrollWidth); ({width:innerWidth, scrollWidth:document.documentElement.scrollWidth})'
Write-Output 'PASS mobile history panel has no horizontal overflow'
B screenshot .verify_tmp_scripts/stage48-mobile-history.png
B viewport 1280x800
B screenshot .verify_tmp_scripts/stage48-desktop-history.png
Write-Output 'STAGE48-MAIN-PATH: all checks passed'
