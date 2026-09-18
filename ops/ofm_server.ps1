<#
OFM sunucusu (Windows): oturum acilisinda otomatik baslayan gozcu (ops/run_server.py).

  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Install    # kur ve baslat
  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Status     # durum + son gunluk
  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Restart    # kod guncellemesinden sonra
  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Stop
  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Uninstall  # durdur ve otomatik baslatmayi kaldir
  powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Deploy     # canli kopyayi main'e tasi + yeniden baslat

Yonetici yetkisi gerekmez: gorev yalnizca bu kullanicinin oturumunda calisir. Gorev Zamanlayici izin vermezse
Baslangic klasorune kisayol konur. Guvenlik duvarina kural EKLEMEZ (ag erisimi bilincli bir karardir).
#>
param(
    [ValidateSet("Install", "Uninstall", "Start", "Stop", "Restart", "Status", "Deploy")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TaskName = "OFM Server"
$Port = if ($env:OFM_PORT) { $env:OFM_PORT } else { "8501" }
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "OFM Server.lnk"

function Get-Pythonw {
    $python = (Get-Command python -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path $python) "pythonw.exe"
    if (-not (Test-Path $pythonw)) { throw "pythonw.exe bulunamadi: $pythonw" }
    return $pythonw
}

function Get-OfmProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -match "ops[\\/]run_server\.py" -or
            ($_.CommandLine -match "streamlit run web_app\.py" -and $_.CommandLine -match "--server\.port $Port")
        )
    }
}

function Test-Health {
    try { return (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/_stcore/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { return $false }
}

function Start-Ofm {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Start-Process -FilePath (Get-Pythonw) -ArgumentList "ops\run_server.py" -WorkingDirectory $Root
    }
    Write-Host "Baslatiliyor; saglik ucu bekleniyor (en fazla 3 dk)..."
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health) { Write-Host "OFM calisiyor: http://localhost:$Port"; return }
        Start-Sleep -Seconds 3
    }
    Write-Warning "Saglik ucu yanit vermedi; logs\ofm_server.log ve logs\streamlit.log dosyalarina bakin."
}

function Stop-Ofm {
    $procs = @(Get-OfmProcesses)
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "Durduruldu ($($procs.Count) surec)."
}

function Install-Ofm {
    $pythonw = Get-Pythonw
    $user = "$env:USERDOMAIN\$env:USERNAME"
    try {
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument "ops\run_server.py" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $trigger.Delay = "PT30S"                                   # Docker Desktop'a acilis payi
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Description "Online Football Manager (OFM) web sunucusu ve dunya turu zamanlayicisi" -Force | Out-Null
        Write-Host "Gorev Zamanlayici gorevi kuruldu: '$TaskName' (oturum acilisinda)."
    } catch {
        Write-Warning "Gorev Zamanlayici gorevi kurulamadi ($($_.Exception.Message)); Baslangic klasorune kisayol konuyor."
        $shell = New-Object -ComObject WScript.Shell
        $link = $shell.CreateShortcut($Shortcut)
        $link.TargetPath = $pythonw
        $link.Arguments = "ops\run_server.py"
        $link.WorkingDirectory = $Root
        $link.Description = "Online Football Manager (OFM) sunucusu"
        $link.Save()
        Write-Host "Kisayol: $Shortcut"
    }
    Stop-Ofm
    Start-Ofm
}

switch ($Action) {
    "Install"   { Install-Ofm }
    "Start"     { Start-Ofm }
    "Stop"      { Stop-Ofm }
    "Restart"   { Stop-Ofm; Start-Sleep -Seconds 2; Start-Ofm }
    "Deploy"    {
        # Canli calisma agacini (bu betigin bulundugu kopya) main'deki son commit'e tasir ve yeniden baslatir.
        git -C $Root checkout --detach main
        if ($LASTEXITCODE -ne 0) { throw "git checkout basarisiz; canli kopyada commit edilmemis degisiklik olabilir." }
        Write-Host ("Canli surum: " + (git -C $Root log --oneline -1))
        Stop-Ofm; Start-Sleep -Seconds 2; Start-Ofm
    }
    "Uninstall" {
        Stop-Ofm
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        if (Test-Path $Shortcut) { Remove-Item $Shortcut -Force }
        Write-Host "Otomatik baslatma kaldirildi."
    }
    "Status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Host ("Gorev: " + $(if ($task) { $task.State } elseif (Test-Path $Shortcut) { "Baslangic kisayolu" } else { "kurulu degil" }))
        Write-Host ("Saglik: " + $(if (Test-Health) { "calisiyor -> http://localhost:$Port" } else { "yanit yok" }))
        Get-OfmProcesses | Select-Object ProcessId, Name, @{n = "Komut"; e = { $_.CommandLine.Substring(0, [Math]::Min(90, $_.CommandLine.Length)) } } | Format-Table -AutoSize | Out-String | Write-Host
        $log = Join-Path $Root "logs\ofm_server.log"
        if (Test-Path $log) { Get-Content $log -Tail 8 }
    }
}
