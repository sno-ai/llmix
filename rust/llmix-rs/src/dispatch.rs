use crate::error::LlmixResult;
use crate::types::{DispatchContext, ProviderResult};
use async_trait::async_trait;
use std::future::Future;

#[async_trait]
pub trait DispatchFn: Send + Sync {
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult>;
}

#[async_trait]
impl<F, Fut> DispatchFn for F
where
    F: Fn(DispatchContext) -> Fut + Send + Sync,
    Fut: Future<Output = LlmixResult<ProviderResult>> + Send,
{
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult> {
        (self)(ctx).await
    }
}
