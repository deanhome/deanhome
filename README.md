# Dean Videographer 3D — WordPress Theme

> This repository also contains **[`serato-analyzer/`](serato-analyzer/README.md)**, a
> separate cross-platform (macOS / Windows) desktop app for Serato DJ libraries
> with a Smart Cover Art Finder. It is unrelated to the theme below.

A cinematic, one-page WordPress theme for a videographer / director of
photography, built around scroll-triggered 3D reveal animations and a
parallax hero and equipment band.

## What's here

```
wp-content/themes/dean-videographer/   <- the actual WordPress theme
  style.css                            <- theme header + all design/CSS
  functions.php                        <- theme setup, enqueue, helpers
  inc/customizer.php                   <- editable text fields (Customizer)
  header.php / footer.php / index.php
  front-page.php                       <- the one-page layout (main file)
  assets/js/scroll.js                  <- 3D scroll-reveal + parallax JS
  assets/images/
    hero-camera.jpg                    <- cinema camera rig (hero + equipment bg)
    portrait-dean.png                  <- videographer portrait (about section)

preview.html                           <- static, no-WordPress-needed preview
                                           of the same design (open directly
                                           in a browser to check the effects)
```

## Installing on WordPress

1. Zip the `dean-videographer` folder:
   ```
   cd wp-content/themes
   zip -r dean-videographer.zip dean-videographer
   ```
2. In WP Admin: **Appearance → Themes → Add New → Upload Theme**, upload
   the zip, then **Activate**.
   (Or copy the folder directly into `wp-content/themes/` via FTP/SFTP if
   you have server access.)
3. Go to **Settings → Reading** and set "Your homepage displays" to
   **A static page** — WordPress needs a page assigned as the front page
   for `front-page.php` to be used. Any published page works as the
   placeholder; the template ignores its content and renders the full
   one-pager itself.
4. Go to **Appearance → Customize → Videographer Site Content** to edit
   all headline/body copy, stats, contact email/phone and social links
   without touching code.
5. Optional: **Appearance → Menus**, create a menu with the anchor links
   (`#about`, `#services`, `#work`, `#equipment`, `#process`, `#contact`)
   and assign it to the "Primary Menu" location — otherwise a sensible
   default menu is used automatically.
6. Optional: **Appearance → Customize → Site Identity** to upload a real
   logo image (falls back to the "DEAN" text logo otherwise).

## Previewing without WordPress

Open `preview.html` directly in a browser (or serve the repo root with
any static server, e.g. `python3 -m http.server`) to see the exact same
design and scroll effects — useful for reviewing changes before copying
them into the PHP templates.

## Swapping in real content

- Replace `assets/images/hero-camera.jpg` and
  `assets/images/portrait-dean.png` with final photography (same
  filenames, or update the paths in `front-page.php` / `style.css`).
- The showreel section (`#work`) currently links to `#` — point the
  `.showreel-frame` anchor in `front-page.php` at a real video (YouTube/
  Vimeo lightbox, or embed a `<video>`/oEmbed) once footage is ready.
- The contact section uses a `mailto:`/`tel:` link. Swap in a plugin
  shortcode (e.g. Contact Form 7 / WPForms) inside `front-page.php`'s
  `#contact` section if you want an actual submission form.

## Notes on the 3D scroll effect

- Sections use `.reveal` + `data-effect="rise|tilt-left|tilt-right|zoom|flip-up"`
  and are animated in via `IntersectionObserver` in `assets/js/scroll.js`
  (see `style.css` section 2 for the transform definitions).
- The hero and equipment sections have an additional lightweight
  parallax layer driven by scroll position (`requestAnimationFrame`,
  no external library).
- Everything degrades gracefully: `prefers-reduced-motion` disables the
  transforms, and a `no-js` fallback shows all content immediately if
  JavaScript fails to load.
