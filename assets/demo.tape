# The README demo. Render with: vhs assets/demo.tape
# Needs a Flask checkout at d086db8 next to this repo, indexed once with
# `mimry index`, and `mimry` on PATH. Uses pwsh because on Windows
# "bash" resolves to WSL.
#   git clone https://github.com/pallets/flask demo-flask
#   cd demo-flask && git checkout d086db8 && mimry index
Output assets/demo.gif

Set Shell "pwsh"
Set FontSize 17
Set Width 1240
Set Height 590
Set Padding 26
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful
Set TypingSpeed 40ms

Hide
Type "Set-Location ../demo-flask"
Enter
Type "function prompt { '$ ' }; Clear-Host"
Enter
Show

Sleep 600ms
Type `mimry preflight "Fix session cookie not refreshed when SESSION_REFRESH_EACH_REQUEST is set"`
Sleep 700ms
Enter
Sleep 5s
Sleep 4s
