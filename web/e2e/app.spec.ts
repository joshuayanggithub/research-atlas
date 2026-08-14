import { test, expect, type Page } from "@playwright/test";

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
  });

  test("title search selects a paper and opens details", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);

    const search = page.getByRole("combobox", { name: "Search papers by title" });
    await search.click();
    await search.fill("learning");
    const firstOption = page.locator("#paper-search-results [role=option]").first();
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

  test("hovering a point shows a preview tooltip", async ({ page }) => {
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
      return points.slice(0, 400);
    });
    expect(candidates.length).toBeGreaterThan(0);
    for (const [fx, fy] of candidates) {
      await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy);
      await page.waitForTimeout(20);
      if (await page.locator(".node-tooltip").count()) break;
    }
    await expect(page.locator(".node-tooltip")).toBeVisible();
  });
});

test.describe("Organization drill-down", () => {
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

    // The researcher list is populated from papers attributed to the unit.
    await expect(page.locator(".org-author-list li").first()).toBeVisible();
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

    await page.getByRole("button", { name: "Last 24mo" }).click();
    await expect(page.getByRole("heading", { name: /Dates Jan 2025/ })).toBeVisible();
    // Range shrinks below the full corpus.
    await expect(dateCount).toHaveText(/[1-9][\d,]* papers in range/);
    const n = parseInt(((await dateCount.textContent()) ?? "").replace(/[^0-9]/g, ""));
    expect(n).toBeGreaterThan(0);
    expect(n).toBeLessThan(full);
  });

  test("the single dual-handle slider narrows the range by dragging", async ({ page }) => {
    await page.goto("/");
    await waitForMap(page);
    const filtersToggle = page.getByRole("button", { name: "Filters", exact: true });
    if (await filtersToggle.isVisible()) await filtersToggle.click();

    // One control with two thumbs (not two separate inputs).
    const startThumb = page.getByRole("slider", { name: "Start month" });
    const endThumb = page.getByRole("slider", { name: "End month" });
    await expect(startThumb).toBeVisible();
    await expect(endThumb).toBeVisible();

    // Drag the start thumb inward (one gesture — reliable and fast) and confirm the range
    // label moves off the corpus start month.
    const box = (await startThumb.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 140, box.y + box.height / 2, { steps: 12 });
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
