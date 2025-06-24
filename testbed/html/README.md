# hgr

## Testbed UI

uzg9jd-codex/사용자가-ui-구조-변경-가능하게-만들기
`testbede/html/testbed.html` loads its layout from `testbede/html/layout.json` by default.
Open the file through a local web server (e.g. `python -m http.server` inside the
`html` directory) so the page can fetch the configuration. If the fetch fails
(e.g. when opening the page directly from the file system) the page falls back
to a built‑in layout.

Use the **레이아웃 편집** button in the page to modify the JSON directly. Changes
are saved to your browser's local storage and applied immediately. The **기본 복원**
button clears the saved layout and reloads the default from `layout.json`.

uu0mfk-codex/사용자가-메모리,-데이터-타입,-이름-변경-가능하게-수정
Within the editor you can add or remove button entries for each panel. You may
edit each button's ID, label and memory address, and choose its optional `type`
from a drop-down list (`BIT`, `BYTE`, `WORD`, `DWORD` or `LWORD`).
