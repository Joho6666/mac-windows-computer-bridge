# Adding images

## Where to put them

Store diagrams and approved screenshots in `docs/images/`. Prefer SVG for diagrams and PNG or JPEG for screenshots/photos. Use descriptive, lowercase filenames such as `architecture.svg` or `windows-worker-flow.png`.

## Embed an image in the root README

Use a repository-relative Markdown link; paths and filename capitalization are case-sensitive:

```markdown
![Short description of the image](docs/images/your-image.png)
```

From another file inside `docs/`, the equivalent relative path is:

```markdown
![Short description](images/your-image.png)
```

Commit the image file and the Markdown reference together. GitHub renders the image in the README; clicking it opens the original file. Keep assets reasonably small and add alternative text that describes the useful information, not just “image”.

## Screenshots and privacy

Crop to the relevant application area and inspect the full image before publishing. Remove account names, email addresses, notifications, customer data, private hostnames/IPs, SSH details, tokens, browser tabs, and unrelated windows. Use a synthetic test document and a dedicated test window. Do not upload a full desktop screenshot just to demonstrate a layout.

The architecture image in this repository is a hand-authored SVG so labels remain crisp at any size and it contains no machine-specific identifiers.
