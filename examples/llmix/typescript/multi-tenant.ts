/**
 * LLMix TypeScript multi-tenant pipeline setup.
 *
 * Shows tenant isolation with separate key pools, budgets,
 * and rate limits per customer.
 *
 * Run with:
 *   OPENAI_API_KEY=sk-... bun run examples/llmix/typescript/multi-tenant.ts
 */

import {
  CallPipeline,
  KeyPool,
  CostTracker,
  BudgetPolicy,
  AIMDConfig,
  openaiDispatch,
  type CallInput,
} from "@snoai/llmix";

interface TenantConfig {
  tenantId: string;
  apiKeys: string[];
  dailyBudgetUsd: number;
  maxConcurrency: number;
}

class TenantPipelineFactory {
  private pipelines = new Map<string, CallPipeline>();

  create(config: TenantConfig): CallPipeline {
    const costTracker = new CostTracker({
      budgetPolicy: new BudgetPolicy({
        dailyLimitUsd: config.dailyBudgetUsd,
        perRequestLimitUsd: config.dailyBudgetUsd * 0.1, // 10% of daily
        alertThreshold: 0.80,
      }),
    });

    const pipeline = new CallPipeline({
      dispatch: openaiDispatch(),
      costTracker,
      aimdConfig: new AIMDConfig({
        initialConcurrency: Math.min(config.maxConcurrency, 10),
        maxConcurrency: config.maxConcurrency,
      }),
    });

    pipeline.setKeyPool("openai", new KeyPool(config.apiKeys));
    this.pipelines.set(config.tenantId, pipeline);
    return pipeline;
  }

  get(tenantId: string): CallPipeline | undefined {
    return this.pipelines.get(tenantId);
  }

  getUsageReport(): Record<string, { spend: number; requests: number }> {
    const report: Record<string, { spend: number; requests: number }> = {};
    for (const [tenantId, pipeline] of this.pipelines) {
      const stats = pipeline.costTracker.dailySummary();
      report[tenantId] = { spend: stats.totalUsd, requests: stats.requestCount };
    }
    return report;
  }
}

async function main(): Promise<void> {
  const factory = new TenantPipelineFactory();

  // Configure two tenants with different limits
  const tenantA = factory.create({
    tenantId: "tenant-a",
    apiKeys: [process.env["OPENAI_API_KEY"]!],
    dailyBudgetUsd: 50.0,
    maxConcurrency: 20,
  });

  factory.create({
    tenantId: "tenant-b",
    apiKeys: [process.env["OPENAI_API_KEY"]!],
    dailyBudgetUsd: 10.0,
    maxConcurrency: 5,
  });

  // Tenant A makes a call — isolated from Tenant B
  const input: CallInput = {
    config: {
      provider: "openai",
      model: "gpt-4.1-mini",
      common: { temperature: 0.5, maxOutputTokens: 64 },
    },
    messages: [{ role: "user", content: "Hello from tenant A!" }],
  };

  const response = await tenantA.call(input);
  console.log(`Tenant A response: ${response.content}`);
  console.log(`Tenant A cost: $${response.costUsd.toFixed(6)}`);

  // Usage report
  console.log("\nUsage report:", factory.getUsageReport());
}

main().catch(console.error);
