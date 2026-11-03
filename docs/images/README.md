# README images

All names, domains and numbers in these images are made up.

To make them again (macOS, needs Homebrew `poppler` for `pdftoppm` and Google Chrome):

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
OUT=/tmp/agencyrecap-shots
uv run python scripts/make_screenshots.py $OUT
pdftoppm -r 110 -png $OUT/report.pdf docs/images/report-page   # report-page-1.png … -3.png
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1200,620 --screenshot=docs/images/dashboard.png "file://$OUT/dashboard.html"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1200,760 --screenshot=docs/images/client-form.png "file://$OUT/client_form.html"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1200,900 --screenshot=docs/images/settings.png "file://$OUT/settings.html"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=1.5 --window-size=1600,900 --screenshot=docs/images/cover.png "file://$PWD/docs/images/src/cover.html"
"$CHROME" --headless=new --hide-scrollbars --force-device-scale-factor=1.5 --window-size=1600,900 --screenshot=docs/images/how-it-works.png "file://$PWD/docs/images/src/how-it-works.html"
```

The two slides (`cover.png`, `how-it-works.png`) are 1600×900, so they also work as social posts or presentation slides.
