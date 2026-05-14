#!/usr/bin/env node
import { pathToFileURL } from "node:url"

import { runCli } from "./config-registry-cli.js"

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
	process.exitCode = await runCli(process.argv.slice(2))
}

export { runCli }
