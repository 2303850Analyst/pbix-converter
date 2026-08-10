[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = "python",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ApplicationArguments
)

& $Python -m pbix_doc @ApplicationArguments
exit $LASTEXITCODE
