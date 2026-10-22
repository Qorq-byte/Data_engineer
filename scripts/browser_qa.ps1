param([string]$BrowseExe = (Join-Path $env:USERPROFILE ".agents/skills/gstack/browse/dist/browse.exe"))
$ErrorActionPreference = 'Stop'
$env:BROWSE_PARENT_PID = '0'
$browser = $BrowseExe
if (!(Test-Path -LiteralPath $browser)) { throw "Supply -BrowseExe with the installed gstack /browse binary" }
function B {
    $result = & $browser @args
    if ($LASTEXITCODE -ne 0) { throw ($result -join "`n") }
    $result
}
function AssertText($expected) {
    $actual = (B text) -join "`n"
    if (!$actual.Contains($expected)) { throw "Expected text missing: $expected`n$actual" }
    "PASS text: $expected"
}
function FreshQuery($text) {
    B click 'button[title="新建对话"]'
    B fill 'textarea[aria-label="数据问题"]' $text
    B click 'button[aria-label="发送查询"]'
}
B goto http://127.0.0.1:8018/workspace
B eval .verify_tmp_scripts/browser_auth.js
B goto http://127.0.0.1:8018/workspace
B js 'window.qaStart = await (await fetch("/qa-counts")).json()'
FreshQuery normal_case
Start-Sleep -Seconds 2
AssertText '查询执行完成 · 3 行'
AssertText '测试模型（MOCK）'
B screenshot .verify_tmp_scripts/stage5-desktop.png
FreshQuery empty_case
Start-Sleep -Seconds 1
AssertText '查询执行完成，结果为 0 行'
FreshQuery error_case
Start-Sleep -Seconds 1
AssertText '查询失败'
FreshQuery slow_case
Start-Sleep -Milliseconds 500
AssertText 'SQL 生成 · 进行中'
B click 'text=取消查询'
Start-Sleep -Milliseconds 200
B click 'button[title="新建对话"]'
Start-Sleep -Seconds 5
B js 'if(document.getElementById("root").textContent.includes("查询执行完成")) throw new Error("Cancelled result leaked"); "PASS cancel/new conversation isolation"'
B js 'const end=await (await fetch("/qa-counts")).json(); const delta=(end["/api/v1/query/stream"]||0)-(window.qaStart["/api/v1/query/stream"]||0); if(delta!==4 || end["/api/v1/query"]) throw new Error(JSON.stringify(end)); ({stream_requests:delta,json_replays:0})'
Write-Output 'PASS exactly four stream requests, zero JSON replays'
B viewport 375x812
B js 'if(document.documentElement.scrollWidth>innerWidth) throw new Error("Mobile overflow"); ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth})'
B js 'const r=document.querySelector("button[aria-label=发送查询]").getBoundingClientRect(); if(r.bottom>innerHeight || r.right>innerWidth || r.top<0) throw new Error("Mobile send button outside viewport"); "PASS mobile input dock is visible"'
B screenshot .verify_tmp_scripts/stage5-mobile.png
B viewport 1280x800
B click 'a:has-text("领域知识")'
Start-Sleep -Milliseconds 500
B click 'text=+ 新建领域'
Start-Sleep -Milliseconds 200
B js 'const dialog=document.querySelector("[role=dialog]"); if(!dialog || !dialog.contains(document.activeElement) || !document.getElementById(dialog.getAttribute("aria-labelledby"))) throw new Error("Modal focus/title"); "PASS dialog name and initial focus"'
B js 'Array.from(document.querySelectorAll("[role=dialog] button")).at(-1).focus()'
B press Tab
B js 'if(document.activeElement.getAttribute("aria-label")!=="关闭对话框") throw new Error("Tab escaped modal"); "PASS modal Tab cycles to first control"'
B fill '[role="dialog"] input[placeholder="例如: healthcare"]' 'keyboard_qa'
B js 'if(document.activeElement.value!=="keyboard_qa") throw new Error("Typing lost focus"); "PASS modal typing keeps focus"'
B press Escape
B js 'if(document.querySelector("[role=dialog]")) throw new Error("Escape did not close"); "PASS Escape closes modal"'
B js 'window.originalFetch=window.fetch; window.fetch=(url,opts)=>String(url).includes("/api/v1/databases") ? Promise.reject(new TypeError("Failed to fetch")) : window.originalFetch(url,opts)'
B click 'a:has-text("数据Schema")'
Start-Sleep -Milliseconds 300
AssertText '数据库信息不可用'
B js 'const text=document.getElementById("root").textContent; if(text.includes("pg_warehouse") || text.includes("sqlite_main")) throw new Error("Fake database shown offline"); window.fetch=window.originalFetch; "PASS offline schema has no fake connections"'
B js 'localStorage.removeItem("nl2sql_token"); localStorage.removeItem("nl2sql_user")'
B goto http://127.0.0.1:8018/login
B snapshot -i
B click 'button:has-text("Sign up")'
Start-Sleep -Milliseconds 250
B snapshot -i
B js 'const state={invitation:!!document.querySelector("input[aria-label=注册邀请码]"),hiddenLoginInputs:document.querySelectorAll("[inert] input").length}; if(!state.invitation || state.hiddenLoginInputs!==2) throw new Error("Invitation form or hidden login focus: "+JSON.stringify(state)); "PASS invitation field and hidden login inert"'
B js 'window.fetch=async(url,opts)=>{window.invitationHeader=opts.headers["X-Registration-Token"]; return new Response(JSON.stringify({detail:"合成注册拒绝"}),{status:403,headers:{"Content-Type":"application/json"}})}'
B fill '.flip-back input[aria-label="注册邀请码"]' synthetic-invite-only
B fill '.flip-back input[aria-label="Username"]' qa_user
B fill '.flip-back input[aria-label="Email"]' qa@example.invalid
B fill '.flip-back input[aria-label="Password"]' test-password-only
B fill '.flip-back input[aria-label="Confirm password"]' test-password-only
B click '.flip-back button[type="submit"]'
B js 'if(window.invitationHeader!=="synthetic-invite-only") throw new Error("Invitation not sent"); "PASS invitation header forwarded (intercepted UI test only)"'
AssertText '合成注册拒绝'
B console
