# WinForms drop target for New Love Plus+ CIA patching.
# Drag a .cia onto the window, or click Browse.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $src
$bat = Join-Path $root "Drop CIA Here to Patch.bat"

$form = New-Object System.Windows.Forms.Form
$form.Text = "New Love Plus+ English Patcher"
$form.Size = New-Object System.Drawing.Size(520, 280)
$form.StartPosition = "CenterScreen"
$form.AllowDrop = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(245, 247, 250)

$label = New-Object System.Windows.Forms.Label
$label.Text = "Drop a New Love Plus+ .cia here"
$label.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$label.AutoSize = $false
$label.TextAlign = "MiddleCenter"
$label.Dock = "Top"
$label.Height = 70
$label.Padding = New-Object System.Windows.Forms.Padding(12)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "Requires the encrypted dump (SHA-1 a9fbd2e6…). Scripts + UI → out\"
$hint.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$hint.ForeColor = [System.Drawing.Color]::FromArgb(80, 90, 100)
$hint.AutoSize = $false
$hint.TextAlign = "MiddleCenter"
$hint.Dock = "Top"
$hint.Height = 40

$pathBox = New-Object System.Windows.Forms.TextBox
$pathBox.ReadOnly = $true
$pathBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$pathBox.Dock = "Top"
$pathBox.Height = 28
$pathBox.Margin = New-Object System.Windows.Forms.Padding(16)
$pathBox.Text = "(no file selected)"

$panel = New-Object System.Windows.Forms.Panel
$panel.Dock = "Top"
$panel.Height = 56
$panel.Padding = New-Object System.Windows.Forms.Padding(16, 8, 16, 8)

$browse = New-Object System.Windows.Forms.Button
$browse.Text = "Browse..."
$browse.Width = 110
$browse.Height = 32
$browse.Left = 16
$browse.Top = 10

$go = New-Object System.Windows.Forms.Button
$go.Text = "Patch"
$go.Width = 110
$go.Height = 32
$go.Left = 140
$go.Top = 10
$go.Enabled = $false
$go.BackColor = [System.Drawing.Color]::FromArgb(40, 120, 200)
$go.ForeColor = [System.Drawing.Color]::White
$go.FlatStyle = "Flat"

$status = New-Object System.Windows.Forms.Label
$status.Text = "Waiting for a .cia drop..."
$status.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$status.Dock = "Bottom"
$status.Height = 36
$status.TextAlign = "MiddleCenter"

$script:ciaPath = $null

function Set-Cia([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) {
        $status.Text = "File not found."
        return
    }
    if ([IO.Path]::GetExtension($path).ToLowerInvariant() -ne ".cia") {
        $status.Text = "Please drop a .cia file."
        return
    }
    $script:ciaPath = $path
    $pathBox.Text = $path
    $go.Enabled = $true
    $status.Text = "Ready — click Patch (or drop another .cia)."
    $form.BackColor = [System.Drawing.Color]::FromArgb(230, 245, 235)
}

$form.Add_DragEnter({
    param($sender, $e)
    if ($e.Data.GetDataPresent([Windows.Forms.DataFormats]::FileDrop)) {
        $e.Effect = [Windows.Forms.DragDropEffects]::Copy
    }
})

$form.Add_DragDrop({
    param($sender, $e)
    $files = [string[]]$e.Data.GetData([Windows.Forms.DataFormats]::FileDrop)
    if ($files -and $files.Count -gt 0) {
        Set-Cia $files[0]
    }
})

$browse.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "CIA files (*.cia)|*.cia|All files (*.*)|*.*"
    $dlg.Title = "Select New Love Plus+ CIA"
    if ($dlg.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) {
        Set-Cia $dlg.FileName
    }
})

$go.Add_Click({
    if (-not $script:ciaPath) { return }
    $go.Enabled = $false
    $browse.Enabled = $false
    $status.Text = "Patching… a console window will show progress."
    $form.Refresh()
    $p = Start-Process -FilePath $bat -ArgumentList "`"$($script:ciaPath)`"" -WorkingDirectory $root -PassThru -Wait
    if ($p.ExitCode -eq 0) {
        $status.Text = "Done. See out\NewLovePlusPlus-EN.cia"
        [System.Windows.Forms.MessageBox]::Show(
            "Patched CIA written to:`n$root\out\NewLovePlusPlus-EN.cia`n`nLayeredFS:`n$root\out\layeredfs\",
            "Patch complete",
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
    } else {
        $status.Text = "Patch failed (exit $($p.ExitCode)). Check the console log."
    }
    $go.Enabled = $true
    $browse.Enabled = $true
})

$panel.Controls.Add($browse)
$panel.Controls.Add($go)
$form.Controls.Add($status)
$form.Controls.Add($panel)
$form.Controls.Add($pathBox)
$form.Controls.Add($hint)
$form.Controls.Add($label)

[void]$form.ShowDialog()
