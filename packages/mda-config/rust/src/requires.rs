use std::net::IpAddr;

use regex::Regex;
use serde_json::Value;

use crate::errors::{ErrorCategory, MdaConfigError, Result};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RequiresEnvironment {
    pub allowed_networks: Vec<String>,
}

pub fn enforce_requires(requires: Option<&Value>, env: &RequiresEnvironment) -> Result<()> {
    let Some(requires) = requires else {
        return Ok(());
    };
    let Some(network) = requires.get("network") else {
        return Ok(());
    };

    let allowed: std::collections::HashSet<&str> =
        env.allowed_networks.iter().map(String::as_str).collect();
    let wildcard = allowed.contains("*");

    if network == "none" {
        return Ok(());
    }

    if network == "local" {
        for host in &env.allowed_networks {
            if !is_local_host(host) {
                return Err(MdaConfigError::with_details(
                    ErrorCategory::RequiresNotSatisfied,
                    "requires.network=local but operator permits non-local host",
                    serde_json::json!({ "key": "network", "host": host }),
                ));
            }
        }
        return Ok(());
    }

    if network == "public" {
        if wildcard {
            return Ok(());
        }
        return Err(MdaConfigError::with_details(
            ErrorCategory::RequiresNotSatisfied,
            "requires.network=public but operator does not grant wildcard '*'",
            serde_json::json!({ "key": "network" }),
        ));
    }

    let Some(hosts) = network.as_array() else {
        return invalid_network_shape(network);
    };
    if hosts
        .iter()
        .any(|host| host.as_str().is_none_or(|s| s.is_empty()))
    {
        return invalid_network_shape(network);
    }
    if wildcard {
        return Ok(());
    }
    for host in hosts.iter().filter_map(Value::as_str) {
        if !is_network_allowed(host, &allowed) {
            return Err(MdaConfigError::with_details(
                ErrorCategory::RequiresNotSatisfied,
                format!("requires.network host '{host}' not in operator allow-list"),
                serde_json::json!({
                    "key": "network",
                    "host": host,
                    "allowed": env.allowed_networks,
                }),
            ));
        }
    }
    Ok(())
}

fn invalid_network_shape(value: &Value) -> Result<()> {
    Err(MdaConfigError::with_details(
        ErrorCategory::RequiresNotSatisfied,
        "requires.network has an invalid shape",
        serde_json::json!({ "key": "network", "reason": "invalid-shape", "got": value }),
    ))
}

fn is_network_allowed(host: &str, allowed: &std::collections::HashSet<&str>) -> bool {
    if allowed.contains(host) {
        return true;
    }
    allowed
        .iter()
        .any(|pattern| host_matches_pattern(host, pattern))
}

fn host_matches_pattern(host: &str, pattern: &str) -> bool {
    if !pattern.contains('*') {
        return host == pattern;
    }
    let escaped = regex::escape(pattern).replace("\\*", "[^.]+");
    Regex::new(&format!("^{escaped}$"))
        .map(|re| re.is_match(host))
        .unwrap_or(false)
}

fn is_local_host(host: &str) -> bool {
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    if host == "localhost"
        || host.ends_with(".localhost")
        || host.ends_with(".local")
        || host.ends_with(".internal")
    {
        return true;
    }
    match host.parse::<IpAddr>() {
        Ok(IpAddr::V4(addr)) => addr.is_loopback() || addr.is_private(),
        Ok(IpAddr::V6(addr)) => addr.is_loopback() || (addr.segments()[0] & 0xfe00) == 0xfc00,
        Err(_) => false,
    }
}
