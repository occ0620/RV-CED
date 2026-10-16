$here=Split-Path -Parent $MyInvocation.MyCommand.Path
$preferred='D:\installpackage\python.exe'
if(Test-Path -LiteralPath $preferred){& $preferred (Join-Path $here 'serve_review.py')}else{python (Join-Path $here 'serve_review.py')}
