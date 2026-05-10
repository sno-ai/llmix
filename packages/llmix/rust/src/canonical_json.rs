use crate::error::LlmixResult;
use serde_json::Value;

pub fn to_string(value: &Value) -> LlmixResult<String> {
    let mut out = String::new();
    write_value(value, &mut out)?;
    Ok(out)
}

fn write_value(value: &Value, out: &mut String) -> LlmixResult<()> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => out.push_str(&number.to_string()),
        Value::String(text) => out.push_str(&serde_json::to_string(text)?),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value(item, out)?;
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');

            let mut keys: Vec<_> = map.keys().collect();
            keys.sort_unstable();

            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(key)?);
                out.push(':');
                write_value(&map[*key], out)?;
            }

            out.push('}');
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::to_string;
    use serde_json::json;

    #[test]
    fn sorts_nested_object_keys_without_spaces() {
        let value = json!({
            "z": 1,
            "a": {
                "b": 2,
                "a": [3, {"z": true, "a": null}]
            }
        });

        let actual = to_string(&value).expect("canonical json should serialize");
        assert_eq!(actual, r#"{"a":{"a":[3,{"a":null,"z":true}],"b":2},"z":1}"#);
    }
}
