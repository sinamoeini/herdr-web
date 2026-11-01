use serde_json::{Map, Value};
use crate::error::{Error, Result};

pub fn object<'value>(value: &'value Value, allowed: &[&str]) -> Result<&'value Map<String, Value>> {
    let object = value.as_object().ok_or_else(|| Error::bad("expected a JSON object"))?;
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(Error::bad(format!("unknown parameter: {key}")));
    }
    Ok(object)
}

pub fn text<'value>(value: &'value Value, name: &str, limit: usize, empty: bool) -> Result<&'value str> {
    let text = value.as_str().ok_or_else(|| Error::bad(format!("{name} must be a string")))?;
    if text.len() > limit || text.contains('\0') || (!empty && text.is_empty()) {
        return Err(Error::bad(format!("{name} must be {}1..{limit} UTF-8 bytes without NUL", if empty { "0 or " } else { "" })));
    }
    Ok(text)
}

pub fn integer(value: &Value, name: &str, min: u64, max: u64) -> Result<u64> {
    value.as_u64().filter(|number| (min..=max).contains(number))
        .ok_or_else(|| Error::bad(format!("{name} must be an integer from {min} through {max}")))
}

pub fn command(value: &Value) -> Result<(&str, Value)> {
    object(value, &["method", "params"])?;
    let method = text(&value["method"], "method", 512, false)?;
    let (allowed, required): (&[&str], &[&str]) = match method {
        "workspace.create" => (&["label", "focus", "cwd"], &[]),
        "workspace.focus" | "workspace.close" => (&["workspace_id"], &["workspace_id"]),
        "workspace.rename" => (&["workspace_id", "label"], &["workspace_id", "label"]),
        "workspace.move" => (&["workspace_id", "insert_index"], &["workspace_id", "insert_index"]),
        "tab.create" => (&["workspace_id", "label", "focus", "cwd"], &["workspace_id"]),
        "tab.focus" | "tab.close" => (&["tab_id"], &["tab_id"]),
        "tab.rename" => (&["tab_id", "label"], &["tab_id", "label"]),
        "tab.move" => (&["tab_id", "insert_index"], &["tab_id", "insert_index"]),
        "pane.split" => (&["target_pane_id", "direction", "focus"], &["target_pane_id", "direction"]),
        "pane.focus" | "pane.close" | "pane.zoom" => (&["pane_id"], &["pane_id"]),
        "pane.rename" => (&["pane_id", "label"], &["pane_id", "label"]),
        "pane.neighbor" | "pane.focus_direction" | "pane.swap" => (&["pane_id", "direction"], &["pane_id", "direction"]),
        "layout.set_split_ratio" => (&["tab_id", "path", "ratio"], &["tab_id", "path", "ratio"]),
        _ => return Err(Error::bad(format!("operation is not allowlisted: {method}"))),
    };
    let params = object(&value["params"], allowed)?;
    for name in required {
        if !params.contains_key(*name) { return Err(Error::bad(format!("missing {name}"))); }
    }
    for (name, value) in params {
        match name.as_str() {
            "focus" => { if !value.is_boolean() { return Err(Error::bad("focus must be Boolean")); } }
            "insert_index" => { integer(value, name, 0, 9999)?; }
            "path" => {
                let path = value.as_array().ok_or_else(|| Error::bad("path must be a Boolean array"))?;
                if path.len() > 32 || !path.iter().all(Value::is_boolean) { return Err(Error::bad("path must have at most 32 Boolean entries")); }
            }
            "ratio" => {
                if !value.as_f64().is_some_and(|ratio| ratio.is_finite() && (0.1..=0.9).contains(&ratio)) {
                    return Err(Error::bad("ratio must be from 0.1 through 0.9"));
                }
            }
            "direction" => {
                let direction = text(value, name, 512, false)?;
                let options = if method == "pane.split" { &["right", "down"][..] } else { &["left", "up", "right", "down"][..] };
                if !options.contains(&direction) { return Err(Error::bad("invalid direction")); }
            }
            _ => { text(value, name, 512, name == "label")?; }
        }
    }
    Ok((method, Value::Object(params.clone())))
}

pub fn argv(value: &Value) -> Result<Vec<String>> {
    let array = value.as_array().ok_or_else(|| Error::bad("argv must be an array"))?;
    if array.len() > 128 { return Err(Error::bad("at most 128 arguments are allowed")); }
    array.iter().map(|argument| text(argument, "argument", 8192, true).map(str::to_owned)).collect()
}

pub fn shell_quote(argument: &str) -> String {
    format!("'{}'", argument.replace('\'', "'\"'\"'"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_invalid_commands_without_a_backend() {
        for value in [
            json!({"method":"server.stop", "params":{}}),
            json!({"method":"tab.close", "params":{"tab_id":"tab", "force":true}}),
            json!({"method":"tab.close", "params":{"tab_id":1}}),
            json!({"method":"tab.close", "params":{"tab_id":""}}),
            json!({"method":"workspace.create", "params":{"focus":1}}),
            json!({"method":"pane.split", "params":{"target_pane_id":"pane", "direction":"left"}}),
            json!({"method":"tab.move", "params":{"tab_id":"tab", "insert_index":10000}}),
            json!({"method":"layout.set_split_ratio", "params":{"tab_id":"tab", "path":[0], "ratio":0.5}}),
            json!({"method":"layout.set_split_ratio", "params":{"tab_id":"tab", "path":[], "ratio":0.95}}),
            json!({"method":"tab.close", "params":{"tab_id":"tab\u{0}"}}),
        ] { assert!(command(&value).is_err(), "accepted {value}"); }
    }

    #[test]
    fn accepts_opaque_ids_without_conflating_them() {
        let value = json!({"method":"pane.swap", "params":{"pane_id":"opaque:猫-1", "direction":"up"}});
        assert_eq!(command(&value).unwrap().1["pane_id"], "opaque:猫-1");
    }

    #[test]
    fn limits_count_utf8_bytes_and_quote_every_argument() {
        assert!(text(&json!("é".repeat(257)), "label", 512, true).is_err());
        assert_eq!(shell_quote(""), "''");
        assert_eq!(shell_quote("a 'b' $HOME\n"), "'a '\"'\"'b'\"'\"' $HOME\n'");
        assert!(argv(&json!(["a\u{0}b"])).is_err());
        assert!(argv(&json!(vec!["arg"; 129])).is_err());
    }
}
