// Codex a7dff904 utils/image/src/lib.rs:55-195. Keep image crate encoding,
// resizing, MIME detection and error spelling identical, not Pillow approximations.
use image::{ColorType, DynamicImage, ImageEncoder, ImageFormat};
use image::codecs::{jpeg::JpegEncoder, png::PngEncoder, webp::WebPEncoder};
use image::imageops::FilterType;
use std::{env, fs, io::{self, Write}, path::Path};

fn encode(image: &DynamicImage, format: ImageFormat) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    let result = match format {
        ImageFormat::Jpeg => JpegEncoder::new_with_quality(&mut bytes, 85).encode_image(image),
        ImageFormat::WebP => WebPEncoder::new_lossless(&mut bytes).write_image(
            image.to_rgba8().as_raw(), image.width(), image.height(), ColorType::Rgba8.into()),
        _ => PngEncoder::new(&mut bytes).write_image(
            image.to_rgba8().as_raw(), image.width(), image.height(), ColorType::Rgba8.into()),
    };
    result.map_err(|e| format!("failed to encode image as {format:?}: {e}"))?;
    Ok(bytes)
}

fn run(path: &Path, original: bool) -> Result<(), String> {
    // core/src/tools/handlers/view_image.rs:120-151: metadata belongs inside
    // the isolated helper as well, not in the credential-owning actor.
    let metadata = fs::metadata(path)
        .map_err(|e| format!("unable to locate image at `{}`: {e}", path.display()))?;
    if !metadata.is_file() {
        return Err(format!("image path `{}` is not a file", path.display()));
    }
    let bytes = fs::read(path).map_err(|e| format!("unable to read image at `{}`: {e}", path.display()))?;
    let format = image::guess_format(&bytes).ok().filter(|f| matches!(
        f, ImageFormat::Png | ImageFormat::Jpeg | ImageFormat::Gif | ImageFormat::WebP));
    let dynamic = image::load_from_memory(&bytes).map_err(|e| {
        let detail = if matches!(e, image::ImageError::Decoding(_)) {
            format!("failed to decode image at {}: {e}", path.display())
        } else {
            let mime = mime_guess::from_path(path).first().map(|m| m.essence_str().to_owned())
                .unwrap_or_else(|| "unknown".to_owned());
            format!("unsupported image `{mime}`")
        };
        format!("unable to process image at `{}`: {detail}", path.display())
    })?;
    let preservable = format.filter(|f| matches!(f, ImageFormat::Png | ImageFormat::Jpeg | ImageFormat::WebP));
    let target = preservable.unwrap_or(ImageFormat::Png);
    let output = if original || (dynamic.width() <= 2048 && dynamic.height() <= 2048) {
        if preservable.is_some() { bytes } else { encode(&dynamic, target)? }
    } else {
        encode(&dynamic.resize(2048, 2048, FilterType::Triangle), target)?
    };
    let mime = match target {
        ImageFormat::Jpeg => "image/jpeg", ImageFormat::WebP => "image/webp", _ => "image/png",
    };
    let mut stdout = io::stdout().lock();
    writeln!(stdout, "{mime}").map_err(|e| e.to_string())?;
    stdout.write_all(&output).map_err(|e| e.to_string())
}

fn main() {
    let mut args = env::args().skip(1);
    let path = args.next().expect("image path");
    if let Err(error) = run(Path::new(&path), args.next().as_deref() == Some("original")) {
        eprint!("{error}");
        std::process::exit(1);
    }
}
