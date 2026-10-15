# Schedule Job Bucket Discovery Pipeline Every 12 Hours in Windows Task Scheduler
# Run this script from an elevated PowerShell prompt or as the current user.

$Action = New-ScheduledTaskAction `
    -Execute "C:\Users\Lenovo\Desktop\abhishe\AI\job-bucket\venv\Scripts\python.exe" `
    -Argument "-m src.pipeline --persist-db --limit 5" `
    -WorkingDirectory "C:\Users\Lenovo\Desktop\abhishe\AI\job-bucket"

# Trigger: Runs every 12 hours starting at midnight
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date -Hour 0 -Minute 0 -Second 0) `
    -RepetitionInterval (New-TimeSpan -Hours 12)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName "JobBucketDiscovery12Hour" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Automated 12-hour job discovery pipeline for Java Backend Developer jobs across Freehire, LinkedIn, and Indeed." `
    -User $env:USERNAME `
    -Force

Write-Host "Task 'JobBucketDiscovery12Hour' successfully registered to run every 12 hours."
