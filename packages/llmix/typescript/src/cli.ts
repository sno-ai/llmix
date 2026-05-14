#!/usr/bin/env node
import { realpath } from "node:fs/promises"
import { fileURLToPath } from "node:url"

import { runCli } from "./config-registry-cli.js"

async function isCliEntrypoint(): Promise<boolean> {
	const invokedPath = process.argv[1]
	if (!invokedPath) {
		return false
	}
	try {
		const [modulePath, realInvokedPath] = await Promise.all([realpath(fileURLToPath(import.meta.url)), realpath(invokedPath)])
		return modulePath === realInvokedPath
	} catch {
		return false
	}
}

if (await isCliEntrypoint()) {
	process.exitCode = await runCli(process.argv.slice(2))
}

export { runCli }
