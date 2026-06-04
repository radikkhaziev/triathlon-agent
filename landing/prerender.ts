// Build-time prerender: render <Landing/> to static HTML and bake it into the
// built dist/index.html so crawlers that don't execute JS (Bing, social, LLM
// bots) still get the full page text. The client still boots normally via
// main.tsx (createRoot replaces the static markup on mount).
//
// Runs after `vite build` (see package.json "build"). Renders with no `window`
// → useWide() = false (mobile column) and detectLang() = 'en', which matches
// <html lang="en"> and the English <head> metadata.
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { readFileSync, writeFileSync } from 'node:fs'
import Landing from './src/Landing'

const FILE = 'dist/index.html'
// Whitespace-tolerant so reformatting index.html (newlines/indent inside the
// root container) doesn't silently skip prerendering.
const ROOT_RE = /<div id="root">\s*<\/div>/

const markup = renderToStaticMarkup(createElement(Landing))
const template = readFileSync(FILE, 'utf8')

if (!ROOT_RE.test(template)) {
  throw new Error(`prerender: <div id="root"> placeholder not found in ${FILE}`)
}

// Function replacer: avoids `$`-pattern interpretation if markup contains them.
writeFileSync(FILE, template.replace(ROOT_RE, () => `<div id="root">${markup}</div>`))
console.log(`prerender: baked ${markup.length} chars of static markup into ${FILE}`)
