const TITLES = {
  toll: ["The toll", "Australia is in a sixth straight year of rising road deaths"],
  where: ["Where", "The extra deaths are in Queensland, New South Wales, Victoria and the west"],
  who: ["Who", "Drivers are the largest gap. People outside cars are rising fastest"],
  when: ["When and which roads", "Late afternoon, and both suburban arterials and high-speed roads"],
  holiday: ["Summer plan", "Use the holiday blitz on the year-round gaps, not a different crash"],
};

const state = { data: null, geo: null, tab: "toll", place: "AUS" };

function el(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstChild;
}

function showTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".page").forEach((page) => {
    page.hidden = page.id !== `page-${tab}`;
  });
  render();
}

function render() {
  if (!state.data) return;
  const page = document.getElementById(`page-${state.tab}`);
  const view = state.data.views[state.place][state.tab];
  const [eyebrow, title] = TITLES[state.tab];
  const means = view.means;
  page.innerHTML = "";
  page.appendChild(el(`<div class="banner"><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p class="lede">${escapeHtml(means.split(". ").slice(0, 2).join(". "))}.</p></div>`));
  page.appendChild(kpiRow(view.kpis));
  if (view.hero.kind === "map") {
    page.appendChild(mapCard(view.hero));
  } else {
    page.appendChild(chartCard(view.hero, true));
  }
  const grid = el('<div class="grid"></div>');
  view.charts.forEach((chart) => grid.appendChild(chartCard(chart, false)));
  if (view.charts.length === 1) grid.classList.add("single");
  page.appendChild(grid);
  page.appendChild(el(`<div class="means"><h2>What this means</h2><p>${escapeHtml(means)}</p></div>`));
}

function kpiRow(kpis) {
  const row = el('<div class="kpis"></div>');
  kpis.forEach((card) => {
    row.appendChild(el(`
      <article class="kpi ${card.status}">
        <div class="status">${escapeHtml(card.status_label)}</div>
        <div class="label">${escapeHtml(card.label)}</div>
        <div class="value">${escapeHtml(String(card.value))}</div>
        <div class="unit">${escapeHtml(card.unit)}</div>
        <p class="take">${escapeHtml(card.takeaway)}</p>
      </article>
    `));
  });
  return row;
}

function chartCard(chart, hero) {
  const card = el(`<article class="card ${hero ? "hero" : ""}"></article>`);
  card.appendChild(el(`<h2>${escapeHtml(chart.title)}</h2>`));
  card.appendChild(el(`<p class="insight">${escapeHtml(chart.insight)}</p>`));
  const holder = el('<div class="chart"></div>');
  holder.appendChild(draw(chart));
  card.appendChild(holder);
  if (chart.exact) card.appendChild(el(`<p class="exact">${escapeHtml(chart.exact)}</p>`));
  return card;
}

function draw(chart) {
  if (chart.kind === "line") return lineChart(chart);
  if (chart.kind === "columns") return columnChart(chart);
  return barChart(chart);
}

function lineChart(chart) {
  const width = 860;
  const height = 360;
  const pad = { l: 54, r: 118, t: 28, b: 36 };
  const points = chart.series.flatMap((series) => series.points);
  const max = Math.max(...points.map((point) => point.y), 1);
  const labels = chart.series[1].points.map((point) => point.x);
  const xOf = (label) => {
    const index = labels.indexOf(label);
    return pad.l + (index / Math.max(labels.length - 1, 1)) * (width - pad.l - pad.r);
  };
  const yOf = (value) => pad.t + (1 - value / (max * 1.08)) * (height - pad.t - pad.b);
  const parts = [`<svg viewBox="0 0 ${width} ${height}" role="img">`];
  parts.push(legend(chart.series));
  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (max * tick) / 4;
    const y = yOf(value);
    parts.push(`<line x1="${pad.l}" x2="${width - pad.r}" y1="${y}" y2="${y}" stroke="#2c415c" />`);
    parts.push(`<text x="${pad.l - 8}" y="${y + 4}" fill="#9aafc4" font-size="12" text-anchor="end">${Math.round(value)}</text>`);
  }
  labels.forEach((label) => {
    if (!label.endsWith("-01")) return;
    const year = Number(label.slice(0, 4));
    if (year % 2 !== 0) return;
    parts.push(`<text x="${xOf(label)}" y="${height - 12}" fill="#9aafc4" font-size="12" text-anchor="middle">${year}</text>`);
  });
  chart.series.forEach((series) => {
    const d = series.points.map((point, index) => `${index ? "L" : "M"}${xOf(point.x)},${yOf(point.y)}`).join(" ");
    parts.push(`<path d="${d}" fill="none" stroke="${series.color}" stroke-width="2.5" ${series.dash ? 'stroke-dasharray="6 5"' : ""} />`);
  });
  const actual = chart.series[0].points.at(-1);
  const pathEnd = chart.series[1].points.at(-1);
  parts.push(callout(xOf(actual.x), yOf(actual.y), `${Math.round(actual.y).toLocaleString()}`, "#ff5d73", -8));
  parts.push(callout(xOf(pathEnd.x) - 8, yOf(pathEnd.y), "half", "#3ee0a0", 14));
  parts.push("</svg>");
  return svg(parts.join(""));
}

function callout(x, y, text, color, dy) {
  return `<text x="${x + 8}" y="${y + dy}" fill="${color}" font-size="13" font-weight="700">${text}</text>`;
}

function legend(series) {
  return series.map((item, index) => `
    <rect x="${54 + index * 220}" y="6" width="18" height="4" fill="${item.color}" rx="2"></rect>
    <text x="${78 + index * 220}" y="12" fill="#9aafc4" font-size="12">${escapeHtml(item.name)}</text>
  `).join("");
}

function barChart(chart) {
  const rows = chart.rows || [];
  if (rows.some((row) => Number(row.value) < 0)) return divergingBarChart(chart);
  const width = 860;
  const gutter = 230;
  const rowH = 44;
  const height = Math.max(rows.length, 1) * rowH + 12;
  const max = Math.max(...rows.map((row) => row.value), 1);
  const plot = width - gutter - 16;
  const parts = [`<svg viewBox="0 0 ${width} ${height}" role="img">`];
  rows.forEach((row, index) => {
    const y = 8 + index * rowH;
    const bar = Math.max(2, (row.value / max) * (plot * 0.62));
    const label = wrapLabel(row.label);
    parts.push(`<text x="0" y="${y + 16}" fill="#f4f7fb" font-size="13">${label[0]}</text>`);
    if (label[1]) parts.push(`<text x="0" y="${y + 30}" fill="#9aafc4" font-size="11">${label[1]}</text>`);
    parts.push(`<rect x="${gutter}" y="${y + 6}" width="${bar}" height="16" rx="4" fill="${chart.color || "#4cc9f0"}"></rect>`);
    parts.push(`<text x="${gutter + bar + 8}" y="${y + 18}" fill="#d5e2ef" font-size="12">${escapeHtml(shortNumber(row.value))}</text>`);
  });
  parts.push("</svg>");
  return svg(parts.join(""));
}

function divergingBarChart(chart) {
  const rows = chart.rows || [];
  const width = 860;
  const gutter = 88;
  const rowH = 44;
  const height = Math.max(rows.length, 1) * rowH + 16;
  const extent = Math.max(...rows.map((row) => Math.abs(Number(row.value))), 1);
  const innerLeft = gutter + 36;
  const innerRight = width - 40;
  const zero = (innerLeft + innerRight) / 2;
  const scale = (zero - innerLeft) / extent;
  const parts = [`<svg viewBox="0 0 ${width} ${height}" role="img">`];
  parts.push(
    `<line class="zero-line" data-zero="1" x1="${zero}" x2="${zero}" y1="4" y2="${height - 4}" stroke="#d5e2ef" stroke-width="1.5"></line>`,
  );
  parts.push(`<text x="${zero}" y="12" fill="#9aafc4" font-size="11" text-anchor="middle">0</text>`);
  rows.forEach((row, index) => {
    const value = Number(row.value);
    const y = 14 + index * rowH;
    const label = wrapLabel(row.label);
    parts.push(`<text x="0" y="${y + 16}" fill="#f4f7fb" font-size="13">${label[0]}</text>`);
    const bar = Math.abs(value) * scale;
    const shown = axisLabel(value);
    const name = escapeHtml(row.label);
    if (value < 0) {
      const x = zero - bar;
      parts.push(
        `<rect data-label="${name}" data-sign="neg" x="${x}" y="${y + 6}" width="${bar}" height="16" rx="4" fill="#3ee0a0"></rect>`,
      );
      parts.push(
        `<text data-bar-label="${name}" x="${x - 6}" y="${y + 18}" fill="#d5e2ef" font-size="12" text-anchor="end">${shown}</text>`,
      );
    } else {
      parts.push(
        `<rect data-label="${name}" data-sign="pos" x="${zero}" y="${y + 6}" width="${Math.max(bar, value === 0 ? 0 : 2)}" height="16" rx="4" fill="${chart.color || "#4cc9f0"}"></rect>`,
      );
      parts.push(
        `<text data-bar-label="${name}" x="${zero + Math.max(bar, 2) + 6}" y="${y + 18}" fill="#d5e2ef" font-size="12">${shown}</text>`,
      );
    }
  });
  parts.push("</svg>");
  return svg(parts.join(""));
}

function axisLabel(value) {
  const rounded = Math.round(value);
  if (Math.abs(value - rounded) < 0.05) return String(rounded);
  return value.toFixed(1);
}

function shortNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (Math.abs(number) >= 10) return Math.round(number).toLocaleString();
  return number.toFixed(1);
}

function wrapLabel(text) {
  const clean = String(text);
  if (clean.length <= 32) return [escapeHtml(clean), ""];
  const cut = clean.lastIndexOf(" ", 32);
  const at = cut > 12 ? cut : 32;
  return [escapeHtml(clean.slice(0, at)), escapeHtml(clean.slice(at).trim())];
}

function columnChart(chart) {
  const rows = chart.rows || [];
  const width = 860;
  const height = chart.rows && chart.rows.length > 12 ? 300 : 280;
  const pad = { l: 44, r: 16, t: 28, b: 32 };
  const max = Math.max(...rows.map((row) => row.value), 1);
  const slot = (width - pad.l - pad.r) / Math.max(rows.length, 1);
  const parts = [`<svg viewBox="0 0 ${width} ${height}" role="img">`];
  let peak = 0;
  rows.forEach((row, index) => {
    if (row.value >= (rows[peak]?.value || 0)) peak = index;
  });
  rows.forEach((row, index) => {
    const barH = (row.value / (max * 1.18)) * (height - pad.t - pad.b);
    const x = pad.l + index * slot + slot * 0.18;
    const y = height - pad.b - barH;
    const w = slot * 0.64;
    const hot = index === peak;
    parts.push(`<rect x="${x}" y="${y}" width="${w}" height="${barH}" rx="3" fill="${hot ? "#ff5d73" : "#4cc9f0"}"></rect>`);
    const numeric = Number.isFinite(Number(row.label));
    const showLabel = rows.length <= 12 || Number(row.label) % 6 === 0;
    if (showLabel) {
      const caption = numeric && rows.length > 12 ? hourLabel(Number(row.label)) : row.label;
      parts.push(`<text x="${x + w / 2}" y="${height - 12}" fill="#9aafc4" font-size="11" text-anchor="middle">${caption}</text>`);
    }
    if (hot) {
      parts.push(`<text x="${x + w / 2}" y="${Math.max(16, y - 6)}" fill="#ffd166" font-size="12" font-weight="700" text-anchor="middle">${Math.round(row.value).toLocaleString()}</text>`);
    }
  });
  parts.push("</svg>");
  return svg(parts.join(""));
}

function hourLabel(hour) {
  if (hour === 0) return "12am";
  if (hour === 6) return "6am";
  if (hour === 12) return "12pm";
  if (hour === 18) return "6pm";
  return "";
}

function mapCard(hero) {
  const card = el('<article class="card"></article>');
  card.appendChild(el(`<h2>${escapeHtml(hero.title)}</h2>`));
  card.appendChild(el(`<p class="insight">${escapeHtml(hero.insight)}</p>`));
  const layout = el('<div class="map-layout"></div>');
  layout.appendChild(drawMap());
  const list = el('<ol class="rank"></ol>');
  const ranked = [...state.data.map].sort((a, b) => b.excess - a.excess);
  ranked.forEach((row) => {
    const mark = row.code === state.place ? " ›" : "";
    list.appendChild(el(`<li><span>${row.name}${mark}</span><b>${row.excess.toLocaleString()} extra</b></li>`));
  });
  layout.appendChild(list);
  card.appendChild(layout);
  card.appendChild(el(`<p class="exact">Exact values: ${ranked.map((row) => `${row.code} ${row.deaths.toLocaleString()} deaths, path ${row.target.toLocaleString()}, ${row.rate} per 100,000`).join("; ")}.</p>`));
  return card;
}

function drawMap() {
  const width = 640;
  const height = 560;
  const lon0 = 112;
  const lon1 = 154;
  const lat0 = -44;
  const lat1 = -9.5;
  const project = (lon, lat) => {
    const x = ((lon - lon0) / (lon1 - lon0)) * (width - 40) + 20;
    const y = ((lat1 - lat) / (lat1 - lat0)) * (height - 40) + 16;
    return [x, y];
  };
  const values = Object.fromEntries(state.data.map.map((row) => [row.code, row.excess]));
  const max = Math.max(...Object.values(values));
  const min = Math.min(...Object.values(values));
  const parts = [`<svg viewBox="0 0 ${width} ${height}" role="img">`];
  state.geo.features.forEach((feature) => {
    const code = feature.properties.code;
    const color = shade(values[code], min, max);
    const rings = feature.geometry.type === "Polygon" ? [feature.geometry.coordinates] : feature.geometry.coordinates;
    rings.forEach((polygon) => {
      polygon.forEach((ring) => {
        const d = ring.map((pair, index) => {
          const [x, y] = project(pair[0], pair[1]);
          return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(" ") + " Z";
        const selected = code === state.place;
        parts.push(`<path d="${d}" fill="${color}" stroke="${selected ? "#ffd166" : "#0c1219"}" stroke-width="${selected ? 2.4 : 1}"></path>`);
      });
    });
  });
  const nudge = {
    WA: [0, 0], NT: [0, -8], SA: [0, 10], QLD: [16, -6],
    NSW: [18, 8], VIC: [6, 16], TAS: [0, 14], ACT: [28, -2],
  };
  state.geo.features.forEach((feature) => {
    const code = feature.properties.code;
    const [x, y] = project(feature.properties.label_lon, feature.properties.label_lat);
    const [dx, dy] = nudge[code] || [0, 0];
    parts.push(`<text x="${x + dx}" y="${y + dy}" fill="#f4f7fb" font-size="13" font-weight="700" text-anchor="middle" stroke="#0c1219" stroke-width="3" paint-order="stroke">${code}</text>`);
  });
  parts.push("</svg>");
  const holder = el('<div class="chart"></div>');
  holder.appendChild(svg(parts.join("")));
  return holder;
}

function shade(value, min, max) {
  const t = max === min ? 1 : (value - min) / (max - min);
  const mix = (a, b) => Math.round(a + (b - a) * t);
  return `rgb(${mix(255, 255)}, ${mix(209, 93)}, ${mix(102, 115)})`;
}

function svg(markup) {
  const holder = document.createElement("div");
  holder.innerHTML = markup;
  return holder.firstChild;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function filters() {
  const bar = document.getElementById("filters");
  state.data.states.forEach((item) => {
    const button = el(`<button type="button" class="pill ${item.code === "AUS" ? "active" : ""}">${item.code === "AUS" ? "Australia" : item.code}</button>`);
    button.addEventListener("click", () => {
      state.place = item.code;
      bar.querySelectorAll(".pill").forEach((pill) => pill.classList.toggle("active", pill === button));
      render();
    });
    bar.appendChild(button);
  });
}

async function main() {
  const [data, geo] = await Promise.all([
    fetch("data/dashboard.json").then((response) => response.json()),
    fetch("data/aus-states.geojson").then((response) => response.json()),
  ]);
  state.data = data;
  state.geo = geo;
  document.getElementById("loading").remove();
  document.getElementById("asof").textContent = `Deaths to ${data.meta.data_current}`;
  document.getElementById("fineprint").textContent = `${data.meta.population} ${data.meta.revision} Page prepared ${data.meta.generated}.`;
  filters();
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });
  showTab("toll");
}

main().catch((error) => {
  document.getElementById("loading").textContent = "The figures could not be loaded.";
  console.error(error);
});
