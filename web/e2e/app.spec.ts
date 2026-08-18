import { test, expect, type Page } from "@playwright/test";

// One-page borderless table fixture. Its prose begins "Table 1 reports...", followed by
// the real "Table 1:" caption above the rows. Routing arXiv PDF requests to this fixture
// deterministically guards both caption disambiguation and below-caption table extraction.
const TABLE_CAPTION_ABOVE_PDF_BASE64 =
  "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";

// Wait until the bundle has loaded and the map shell is present (loading text gone).
async function waitForMap(page: Page) {
  await expect(page.getByText("Loading the research map…")).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByRole("main", { name: "Research map" })).toBeVisible();
}

test.describe("Research Visualizer", () => {
  test("loads the map, corpus summary, and a filled canvas", async ({ page }, testInfo) => {
    // Attach the console-error listener BEFORE navigating so load-time errors are caught.
    const errors: string[] = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));

    await page.goto("/");
    await waitForMap(page);

    // The corpus summary (mono readout) is intentionally hidden on the narrow mobile topbar.
    if (testInfo.project.name !== "mobile") {
      await expect(page.getByText(/\d[\d,]* papers ·/)).toBeVisible();
    } else {
      await expect(page.getByText(/\d[\d,]* papers ·/)).toHaveCount(1); // present, visually hidden
    }

    // The deck.gl canvas must contain NON-BACKGROUND pixels — actually read the framebuffer
    // (preserveDrawingBuffer is off, so re-render into a 2D canvas via drawImage and sample).
    const painted = await page.evaluate(() => {
      const src = document.querySelector("canvas") as HTMLCanvasElement | null;
      if (!src || src.width === 0) return false;
      const off = document.createElement("canvas");
      off.width = src.width;
      off.height = src.height;
      const ctx = off.getContext("2d");
      if (!ctx) return false;
      ctx.drawImage(src, 0, 0);
      const { data } = ctx.getImageData(0, 0, off.width, off.height);
      // Background is ~rgb(7,9,13). Count pixels that clearly differ from it.
      let lit = 0;
      for (let i = 0; i < data.length; i += 4) {
        if (Math.abs(data[i] - 7) + Math.abs(data[i + 1] - 9) + Math.abs(data[i + 2] - 13) > 24) lit++;
      }
      return lit > 500; // thousands of points should light up far more than this
    });
    expect(painted).toBe(true);

    // No unexpected console errors (a favicon 404 is the only tolerated one).
    await page.waitForTimeout(500);
    expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("research-map.png"), fullPage: true });
  });

  test("title search selects a paper and opens details", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.click();
    await search.fill("learning");
    const firstOption = page.locator('#paper-search-results [role=option][data-kind="paper"]').first();
    await expect(firstOption).toBeVisible();
    await firstOption.click();

    // Details panel opens with a citations/paper tablist wired to a tabpanel.
    await expect(page.getByRole("tablist", { name: "Paper detail view" })).toBeVisible();
    await expect(page.getByRole("tabpanel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Related works" })).toBeVisible();

    // Switching to the Paper tab shows the preview section (first-page render OR fallback).
    await page.getByRole("tab", { name: "Paper" }).click();
    await expect(page.getByRole("heading", { name: "Paper preview" })).toBeVisible();
  });

  test("label search navigates to a named semantic region", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.fill("vision-language models");
    const labelOption = page
      .locator('#paper-search-results [role=option][data-kind="label"]')
      .first();
    await expect(labelOption).toContainText("Map label");
    await expect(labelOption).toContainText(/Vision-Language Models/i);
    await labelOption.click();

    await expect(search).toHaveValue("");
    await expect(page.getByRole("tablist", { name: "Paper detail view" })).toHaveCount(0);
  });

  test("OpenAlex citation metadata is presented as a count", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.fill("Recursive Language Models");
    const exactPaper = page
      .locator('#paper-search-results [role=option][data-kind="paper"]')
      .filter({ has: page.getByText("Recursive Language Models", { exact: true }) })
      .first();
    await expect(exactPaper).toBeVisible();
    await exactPaper.click();

    const panel = page.getByRole("dialog", { name: "Paper details" });
    await expect(
      panel.getByRole("heading", { name: "Recursive Language Models", exact: true }),
    ).toBeVisible();
    await expect(panel.getByText("citation count unavailable", { exact: false })).toHaveCount(0);
    // Scope to the header meta line: every linked paper in the citation explorer also renders
    // an "N citations" label, so an unscoped match is ambiguous once the graph is populated.
    await expect(panel.locator(".meta.subtle").first()).toContainText(/\d[\d,]* citations/i);
    await expect(panel.getByRole("heading", { name: "Citation network" })).toBeVisible();
  });

  test("extracts a borderless Table 1 below its real caption", async ({ page }) => {
    test.slow(); // the real 271k bundle loads before the deterministic mocked PDF is parsed
    await page.route("https://arxiv.org/pdf/**", (route) =>
      route.fulfill({
        contentType: "application/pdf",
        headers: { "access-control-allow-origin": "*" },
        body: Buffer.from(TABLE_CAPTION_ABOVE_PDF_BASE64, "base64"),
      }),
    );
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.fill("Recursive Language Models");
    await page
      .locator('#paper-search-results [role=option][data-kind="paper"]')
      .filter({ has: page.getByText("Recursive Language Models", { exact: true }) })
      .first()
      .click();

    const table = page.locator(".first-figure-crop").filter({ hasText: "Table 1" });
    // The mocked PDF is parsed by pdf.js on the same contended main thread (D33).
    await expect(table).toBeVisible({ timeout: 90_000 });
    const size = await table.locator("canvas").evaluate((canvas) => ({
      width: canvas.width,
      height: canvas.height,
    }));
    expect(size.width).toBeGreaterThan(500);
    expect(size.height).toBeGreaterThan(100);
    // Guards that the preceding "Table 1 reports…" prose block was excluded — that would add
    // hundreds of px. The bound allows for figureExtract's CROP_PAD, which deliberately pads
    // every crop so glyph ascenders/descenders are not shaved off.
    expect(size.height).toBeLessThan(600);
  });

  test("hovering a point shows a preview tooltip", async ({ page }) => {
    // No per-test override here: this test sweeps up to 160 pointer positions, and every one of
    // them polls the same main thread that SwiftShader is saturating during load (D33). The old
    // 45 s cap expired inside the first boundingBox() call, before a single hover was attempted.
    test.slow();
    await page.goto("/");
    await waitForMap(page);
    // Derive hover candidates from colored framebuffer pixels instead of assuming a
    // particular map layout. A rebuilt projection can move every dense region while still
    // being perfectly valid, which made the old four hard-coded coordinates brittle.
    const canvas = page.locator("canvas").first();
    const box = (await canvas.boundingBox())!;
    const candidates = await canvas.evaluate((src: HTMLCanvasElement) => {
      const off = document.createElement("canvas");
      off.width = src.width;
      off.height = src.height;
      const ctx = off.getContext("2d");
      if (!ctx) return [];
      ctx.drawImage(src, 0, 0);
      const { data } = ctx.getImageData(0, 0, off.width, off.height);
      const points: [number, number][] = [];
      // Saturated pixels overwhelmingly belong to colored paper markers, rather than the
      // gray labels/edges. Sampling every fourth device pixel keeps the sweep inexpensive.
      for (let y = 2; y < off.height - 2; y += 4) {
        for (let x = 2; x < off.width - 2; x += 4) {
          const i = (y * off.width + x) * 4;
          const r = data[i], g = data[i + 1], b = data[i + 2];
          if (Math.max(r, g, b) - Math.min(r, g, b) > 35 && Math.max(r, g, b) > 70) {
            points.push([x / off.width, y / off.height]);
          }
        }
      }
      // Sample across the whole framebuffer rather than sweeping hundreds of adjacent
      // top-left pixels; the 174 MB production bundle leaves less of the default timeout
      // for pointer events on slower browser runs.
      const stride = Math.max(1, Math.floor(points.length / 160));
      return points.filter((_, index) => index % stride === 0).slice(0, 160);
    });
    expect(candidates.length).toBeGreaterThan(0);
    for (const [fx, fy] of candidates) {
      await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy);
      await page.waitForTimeout(10);
      if (await page.locator(".node-tooltip").isVisible()) break;
    }
    await expect(page.locator(".node-tooltip")).toBeVisible();
  });

  // Not runnable on this machine — see D33 and the #10 notes. A canvas click is move+down+up,
  // and each of those waits on the main thread that SwiftShader saturates rendering ~900k
  // points without a GPU; a single page.mouse.click did not return inside a 540 s budget, even
  // with an author filter leaving 7 visible points (the filter runs on the GPU, so every vertex
  // is still processed). Hover — one event — does complete, and the hover test above proves
  // deck.gl picking fires. Un-skip on a machine with a real GPU.
  test.skip("clicking a point on the map canvas selects that paper", async ({ page }) => {
    test.slow();
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.fill("Eliot Xing");
    await page
      .locator('#paper-search-results [role=option][data-kind="author"]')
      .first()
      .locator("button")
      .click();
    await expect(page.getByRole("region", { name: "Active filters" })).toContainText("Eliot Xing");

    // Framebuffer sampling is no use here: with the filter applied the map is idle, and
    // drawImage on a WebGL canvas without preserveDrawingBuffer returns an empty buffer unless
    // a frame was just drawn. Probe outward from the centre the view focused on instead.
    const canvas = page.locator("canvas").first();
    const box = (await canvas.boundingBox())!;
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    const offsets: [number, number][] = [[0, 0]];
    for (let r = 6; r <= 48; r += 6) {
      for (const [dx, dy] of [[1, 0], [0, 1], [-1, 0], [0, -1], [1, 1], [-1, -1]] as const) {
        offsets.push([dx * r, dy * r]);
      }
    }

    const details = page.getByRole("tablist", { name: "Paper detail view" });
    for (const [dx, dy] of offsets) {
      await page.mouse.move(cx + dx, cy + dy);
      await page.mouse.click(cx + dx, cy + dy);
      await page.waitForTimeout(250);
      if (await details.count()) break;
    }
    await expect(details).toBeVisible();
  });
});

test.describe("Organization drill-down", () => {
  test("shows roster-backed neolabs with provenance and filters their papers", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    // Redwood Research has no ROR-verified institutional authorship in the corpus; it appears
    // through a reviewed author roster, and must SAY so rather than look like the others.
    const redwood = page.getByRole("button", { name: /Redwood Research/ });
    await expect(redwood).toBeVisible();
    await expect(redwood).toContainText(/roster/i);

    await redwood.click();
    const bar = page.getByRole("region", { name: "Active filters" });
    await expect(bar).toBeVisible();
    await expect(bar).toContainText("Redwood Research");
  });

  test("expands a university into evidence-backed departments/labs", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    // On mobile the filters live behind a toggle.
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    await page.getByRole("button", { name: "Expand Carnegie Mellon University" }).click();
    await expect(page.getByRole("button", { name: /Robotics Institute/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Language Technologies Institute/ })).toBeVisible();
  });

  test("selecting a lab reveals org-scoped researchers", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    await page.getByRole("button", { name: "Expand Carnegie Mellon University" }).click();
    await page.getByRole("button", { name: /Robotics Institute/ }).click();
    await page.getByRole("button", { name: /Top researchers/ }).click();

    // Precomputed by s10 (D36): the browser can no longer count author ids per paper.
    await expect(page.locator(".org-author-list li").first()).toBeVisible();
  });

  test("selecting an author filters the map and names them in the filter bar", async ({ page }) => {
    // Guards D32: the author index arrives as chunks, and each chunk must hand consumers a NEW
    // array. When it did not, this exact flow applied the filter (the map showed the right
    // papers) while the filter bar and author panel rendered nothing at all.
    test.slow();
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers, authors, or map labels" });
    await search.fill("Eliot Xing");
    const authorOption = page
      .locator('#paper-search-results [role=option][data-kind="author"]')
      .first();
    await expect(authorOption).toContainText("Author");
    await authorOption.locator("button").click();

    // The filter bar names the person and counts what survived the filter.
    const bar = page.getByRole("region", { name: "Active filters" });
    await expect(bar).toBeVisible();
    await expect(bar).toContainText("Eliot Xing");

    // The author panel resolves the OpenAlex id out of the author-papers shard (D32).
    const panel = page.getByRole("region", { name: "Selected authors" });
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("link", { name: /OpenAlex/ })).toHaveAttribute(
      "href",
      /openalex\.org\/A\d+/,
    );
  });

  test("importing a reading list filters the map to those papers", async ({ page }) => {
    // Guards D38. The fixture holds three landmark arXiv papers (matched by arXiv id, by an
    // arXiv DOI, and by id again) plus one deliberate non-match, so the reported count is a
    // real assertion about the matcher rather than "some number appeared".
    test.slow();
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    const panel = page.getByRole("region", { name: "Reading list" });
    await expect(panel).toBeVisible();
    await panel.locator('input[type="file"]').setInputFiles("e2e/fixtures/reading-list.json");

    // 3 of 4 — the book is not in a CS/AI arXiv corpus and must be reported, not hidden.
    await expect(panel).toContainText(/3 of 4 entries matched/, { timeout: 90_000 });

    // Importing activates every list it found, so the map already shows the library.
    const bar = page.getByRole("region", { name: "Active filters" });
    await expect(bar).toContainText("1. Finished");
    await expect(bar).toContainText("2. Understood");
    await expect(bar).toContainText(/^3 of/);

    // Each list is a chip that toggles off, leaving the rest.
    await panel.locator("button.chip").first().click();
    await expect(bar).toContainText(/^1 of/);
    await expect(bar).not.toContainText("1. Finished");
  });

  test("org search surfaces a child unit and its parent", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    await page
      .getByRole("textbox", { name: "Search organizations, departments, and labs" })
      .fill("fair");
    await expect(page.getByRole("button", { name: /FAIR \(Facebook AI Research\)/ })).toBeVisible();
  });

  test("org search finds a non-curated corpus institution (full directory)", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    await page
      .getByRole("textbox", { name: "Search organizations, departments, and labs" })
      .fill("tsinghua");
    // Not a curated seed org — comes from the corpus-wide directory.
    const search = page.getByRole("textbox", { name: "Search organizations, departments, and labs" });
    const tsinghua = page.getByRole("button", { name: /Tsinghua University/ });
    await expect(tsinghua).toBeVisible();
    await tsinghua.click();
    await expect(page.getByText(/papers in range \(filtered\)/)).toBeVisible();

    // Clearing the search must NOT strand the selection: it stays as a removable chip.
    await search.fill("");
    const chip = page.getByRole("button", { name: /Remove Tsinghua University/ });
    await expect(chip).toBeVisible();
    await chip.click();
    await expect(page.getByText(/papers in range \(filtered\)/)).toHaveCount(0);
  });
});

test.describe("Date filter", () => {
  test("a preset narrows the corpus and updates the in-range count", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    // Derive the full corpus size from the UI rather than hardcoding it, so the test
    // survives a corpus rebuild. Wait for the count to render before reading it.
    const dateCount = page.locator(".date-count");
    await expect(dateCount).toHaveText(/[1-9][\d,]* papers in range/);
    // textContent is stable during the mobile filter drawer's open/close transition;
    // innerText can briefly be empty while the element is not being laid out.
    const full = parseInt(((await dateCount.textContent()) ?? "").replace(/[^0-9]/g, ""));
    expect(full).toBeGreaterThan(0);

    // This corpus is shorter than 24 months, so that preset correctly equals All. Twelve
    // months is guaranteed to narrow the current two-year fixture.
    await page.getByRole("button", { name: "Last 12mo" }).click();
    await expect(page.getByRole("button", { name: "Last 12mo" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // Range shrinks below the full corpus.
    await expect(dateCount).toHaveText(/[1-9][\d,]* papers in range/);
    const n = parseInt(((await dateCount.textContent()) ?? "").replace(/[^0-9]/g, ""));
    expect(n).toBeGreaterThan(0);
    expect(n).toBeLessThan(full);
  });

  test("dragging the publication histogram narrows the range", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    // The histogram itself is the only date-range control; its overlaid handles preserve
    // keyboard accessibility without adding a second slider below the bars.
    const histogram = page.getByRole("group", { name: "Publication date range histogram" });
    const startThumb = page.getByRole("slider", { name: "Start month" });
    const endThumb = page.getByRole("slider", { name: "End month" });
    await expect(histogram).toBeVisible();
    await expect(startThumb).toBeVisible();
    await expect(endThumb).toBeVisible();

    // Drag directly over the left edge of the bars and confirm the range label moves off the
    // corpus start month.
    const box = (await histogram.boundingBox())!;
    await page.mouse.move(box.x + 4, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + Math.min(140, box.width * 0.35), box.y + box.height / 2, { steps: 12 });
    await page.mouse.up();
    await expect(page.getByRole("heading", { name: /Dates/ })).not.toHaveText(/Dates Jan 2020 –/);
  });
});

test.describe("Load failure", () => {
  test("shows a clear error when the bundle is missing (route-mocked 404)", async ({ page }) => {
    await page.route("**/data/manifest.json", (route) => route.fulfill({ status: 404 }));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Failed to load data" })).toBeVisible();
    await expect(page.getByText(/pipeline.run_all/)).toBeVisible();
  });
});
