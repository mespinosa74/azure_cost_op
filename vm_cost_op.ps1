$repoURL = "https://github.com/mespinosa74/azure_cost_op.git"
$repoName = "azure_cost_op"

# Check if azure-identity is already installed
$checkInstalled = python -m pip show azure-identity 2>$null

if ($LASTEXITCODE -eq 0) {
    Write-Host "azure-identity is already installed"
} else {
    Write-Host "Installing azure-identity..."
    pip install azure-identity --user
}

if (Test-Path $repoName) {
    Write-Host "Repository already exists at $repoName"
} else {
    Write-Host "Cloning repository..."
    git clone $repoURL
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to clone repository"
        exit 1
    }
    Write-Host "Repository cloned successfully"
}

# Install all dependencies
Write-Host "Installing dependencies from requirements.txt..."
pip install -r ./$repoName/requirements.txt --user

# Check Azure authentication
Write-Host "Checking Azure authentication..."
az account show 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Not logged in to Azure. Please run 'az login' first."
    exit 1
}

# Run the script
Write-Host "Running Azure VM Cost Comparison Tool..."
python ./$repoName/azure_vm_cost_comparison.py