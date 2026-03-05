param(
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"

function Get-DefaultMessage {
    param([string[]]$Files)

    if ($Files.Count -eq 0) {
        return "chore: update project files"
    }

    $preview = $Files | Select-Object -First 3
    $remaining = $Files.Count - $preview.Count
    if ($remaining -gt 0) {
        return "chore: update $($preview -join ', ') (+$remaining more)"
    }
    return "chore: update $($preview -join ', ')"
}

git rev-parse --is-inside-work-tree *> $null

$status = git status --porcelain
if (-not $status) {
    Write-Host "No changes to commit."
    exit 0
}

git add -A

$changedFiles = @(git diff --cached --name-only)
$hasHead = $false
cmd /c "git rev-parse --verify HEAD >NUL 2>NUL"
if ($LASTEXITCODE -eq 0) {
    $hasHead = $true
}

if (-not $Message) {
    if (-not $hasHead) {
        $Message = "chore: initial project scaffold"
    } else {
        $Message = Get-DefaultMessage -Files $changedFiles
    }
}

git commit -m $Message

$branch = git branch --show-current
if (-not $branch) {
    Write-Host "Committed, but current branch could not be determined."
    exit 0
}

cmd /c "git rev-parse --abbrev-ref --symbolic-full-name @{u} >NUL 2>NUL"
if ($LASTEXITCODE -eq 0) {
    git push
    exit 0
}

$remotes = @(git remote)
if ($remotes.Count -eq 0) {
    Write-Host "Committed locally. No Git remote is configured yet."
    Write-Host "Add one, then run: git push -u origin $branch"
    exit 0
}

$remote = if ($remotes -contains "origin") { "origin" } else { $remotes[0] }
git push -u $remote $branch
