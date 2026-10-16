# `test/images/` — sample input images (not in Git)

> Only this README is tracked. The images are personal test data and are not published.

The generation smoke tests and e2e scripts expect character images in this layout:

```text
test/images/
├── ALICE/
│   ├── ALICE_full_front_view.png      # primary image used by the smoke / e2e tests
│   ├── ALICE_full_back_view.png
│   ├── ALICE_full_side_view.png
│   ├── ALICE_face_view.png
│   ├── ALICE_face_side_view.png
│   └── ALICE_ Detail_Reference_Sheet.png
└── ANIME_CHARACTER_01/
    ├── Alice.png
    └── Alice_Anime_form.png
```

Use your own images (PNG / JPEG / WEBP, at least 64 px per side, ≤ 25 MB). Most scripts also accept an explicit
path, for example:

```powershell
.\backend\python\python.exe -s test\e2e\e2e_generate.py --image path\to\your_image.png
```

Only add images you have the rights to use.
