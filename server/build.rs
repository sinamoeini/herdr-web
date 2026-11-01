use std::{env, fs, path::{Path, PathBuf}};

fn content_type(path: &Path) -> Option<&'static str> {
    if matches!(path.file_name()?.to_str()?, "LICENSE" | "LICENSES.md" | "xterm-LICENSE" | "highlight-LICENSE" | "addon-fit-LICENSE" | "SHA256SUMS") {
        return Some("text/plain; charset=utf-8");
    }
    Some(match path.extension()?.to_str()? {
        "html" => "text/html; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "ogg" => "audio/ogg",
        "txt" => "text/plain; charset=utf-8",
        _ => return None,
    })
}

fn collect(directory: &Path, files: &mut Vec<PathBuf>) {
    println!("cargo:rerun-if-changed={}", directory.display());
    for entry in fs::read_dir(directory).expect("read public directory") {
        let entry = entry.expect("read public entry");
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with('.') || matches!(name.as_ref(), "node_modules" | "target" | "package" | "test-results") {
            continue;
        }
        let kind = entry.file_type().expect("asset type");
        assert!(!kind.is_symlink(), "embedded assets must not be symlinks");
        if kind.is_dir() {
            collect(&entry.path(), files);
        } else if kind.is_file() && content_type(&entry.path()).is_some() {
            files.push(entry.path());
        }
    }
}

fn main() {
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
        .join("../public").canonicalize().expect("public directory");
    let mut files = Vec::new();
    collect(&root, &mut files);
    files.sort_by_key(|path| path.strip_prefix(&root).unwrap().to_str().unwrap().replace('\\', "/"));
    let mut table = String::from("pub static ASSETS: &[(&str, &str, &[u8])] = &[\n");
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        let relative = path.strip_prefix(&root).unwrap().to_str().unwrap().replace('\\', "/");
        let mime = content_type(&path).unwrap();
        table.push_str(&format!("({:?}, {:?}, include_bytes!({:?})),\n", format!("/{relative}"), mime, path));
    }
    table.push_str("];\n");
    fs::write(PathBuf::from(env::var_os("OUT_DIR").unwrap()).join("assets.rs"), table)
        .expect("write embedded asset table");
}
