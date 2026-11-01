const pixelLimit = 8388608;
const sceneByteLimit = 16 * 1024 * 1024;
const encodedLimit = 24 * 1024 * 1024;
const cacheLimit = 32 * 1024 * 1024;

function integer(value, name, min, max) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`Invalid ${name}`);
}

export function validateViewport(viewport) {
  integer(viewport.cols, "cols", 1, 500);
  integer(viewport.rows, "rows", 1, 200);
  for (const [name, max] of [["cw", 100], ["ch", 200]]) {
    if (!Number.isFinite(viewport[name]) || viewport[name] < 1 || viewport[name] > max) throw new Error(`Invalid ${name}`);
  }
  return { cols: viewport.cols, rows: viewport.rows, cw: viewport.cw, ch: viewport.ch };
}

export function validateScene(scene) {
  if (!scene || !Array.isArray(scene.assets) || !Array.isArray(scene.placements) || !Array.isArray(scene.retained)) throw new Error("Invalid image scene");
  if (scene.assets.length > 1024 || scene.retained.length > 1024 || scene.placements.length > 10000) throw new Error("Image scene has too many records");
  const validKey = key => typeof key === "string" && key.length > 0 && key.length <= 1024;
  const retained = new Set(scene.retained);
  if (retained.size !== scene.retained.length || !scene.retained.every(validKey)) throw new Error("Invalid retained asset keys");
  const keys = new Set();
  let bytes = 0;
  for (const asset of scene.assets) {
    if (!validKey(asset.key) || keys.has(asset.key) || !retained.has(asset.key)) throw new Error("Invalid or duplicate image asset key");
    keys.add(asset.key);
    integer(asset.width, "image width", 1, pixelLimit);
    integer(asset.height, "image height", 1, pixelLimit);
    if (asset.width * asset.height > pixelLimit) throw new Error("Image pixel limit exceeded");
    integer(asset.format, "image format", 0, 2);
    if (typeof asset.data !== "string" || asset.data.length > encodedLimit || asset.data.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(asset.data)) throw new Error("Invalid base64 image");
    const length = asset.data.length / 4 * 3 - (asset.data.endsWith("==") ? 2 : asset.data.endsWith("=") ? 1 : 0);
    bytes += length;
    if (bytes > sceneByteLimit) throw new Error("Scene exceeds 16 MiB");
    if (asset.format < 2 && length !== asset.width * asset.height * (asset.format === 0 ? 3 : 4)) throw new Error("Image channel length mismatch");
  }
  for (const placement of scene.placements) {
    if (!validKey(placement.key) || !retained.has(placement.key)) throw new Error("Placement asset is not retained");
    for (const name of ["id", "x", "y", "cols", "rows", "sx", "sy", "sw", "sh", "ox", "oy", "z"]) {
      if (!Number.isFinite(placement[name]) || Math.abs(placement[name]) > 10000000) throw new Error(`Invalid placement ${name}`);
    }
    for (const name of ["cols", "rows", "sx", "sy", "sw", "sh"]) if (placement[name] < 0) throw new Error(`Negative placement ${name}`);
  }
  return scene;
}

async function decode(asset) {
  const bytes = Uint8Array.from(atob(asset.data), character => character.charCodeAt(0));
  if (asset.format === 2) {
    const header = new DataView(bytes.buffer);
    if (bytes.length < 33 || header.getUint32(0) !== 0x89504e47 || header.getUint32(4) !== 0x0d0a1a0a || header.getUint32(8) !== 13 || header.getUint32(12) !== 0x49484452 || header.getUint32(16) !== asset.width || header.getUint32(20) !== asset.height) throw new Error("PNG dimensions or signature mismatch");
    return createImageBitmap(new Blob([bytes], { type: "image/png" }));
  }
  const rgba = new Uint8ClampedArray(asset.width * asset.height * 4);
  if (asset.format === 1) rgba.set(bytes);
  else {
    for (let source = 0, destination = 0; source < bytes.length; source += 3, destination += 4) {
      rgba[destination] = bytes[source]; rgba[destination + 1] = bytes[source + 1]; rgba[destination + 2] = bytes[source + 2]; rgba[destination + 3] = 255;
    }
  }
  return createImageBitmap(new ImageData(rgba, asset.width, asset.height));
}

export class ImageScenes {
  constructor(canvas, report = console.error) {
    this.canvas = canvas;
    this.report = report;
    this.cache = new Map();
    this.encoded = new Map();
    this.generation = 0;
    this.revision = 0;
    this.disposed = false;
    this.running = false;
    canvas.classList.add("web-image-canvas");
    canvas.style.pointerEvents = "none";
  }
  viewport(viewport) { this.size = validateViewport(viewport); this.draw(); }
  receive(message) {
    if (this.disposed) return;
    if (typeof message !== "string" || message.length > encodedLimit) throw new Error("Encoded scene exceeds 24 MiB");
    this.accept(validateScene(JSON.parse(message)));
  }
  accept(scene) {
    if (this.disposed) return;
    validateScene(scene);
    this.scene = scene;
    this.revision += 1;
    const retained = new Set(scene.retained);
    for (const [key, image] of this.cache) if (!retained.has(key)) { image.bitmap.close(); this.cache.delete(key); }
    for (const key of this.encoded.keys()) if (!retained.has(key)) this.encoded.delete(key);
    for (const asset of scene.assets) if (!this.cache.has(asset.key)) this.encoded.set(asset.key, asset);
    let pendingBytes = 0;
    for (const asset of this.encoded.values()) pendingBytes += asset.data.length;
    if (pendingBytes > encodedLimit) { this.reset(); throw new Error("Pending encoded assets exceed 24 MiB"); }
    this.draw();
    if (!this.running) void this.drain();
  }
  async drain() {
    this.running = true;
    const generation = this.generation;
    try {
      while (!this.disposed && generation === this.generation && this.encoded.size) {
        const [key, asset] = this.encoded.entries().next().value;
        this.encoded.delete(key);
        const decodedBytes = asset.width * asset.height * 4;
        let used = decodedBytes;
        for (const image of this.cache.values()) used += image.bytes;
        if (used > cacheLimit) throw new Error("Decoded image cache exceeds 32 MiB");
        const bitmap = await decode(asset);
        if (this.disposed || generation !== this.generation || !this.scene?.retained.includes(key)) { bitmap.close(); continue; }
        this.cache.get(key)?.bitmap.close();
        this.cache.set(key, { bitmap, bytes: decodedBytes });
        this.draw();
      }
    } catch (error) {
      if (!this.disposed && generation === this.generation) { this.reset(); this.report(error); }
    } finally {
      this.running = false;
      if (!this.disposed && this.encoded.size) void this.drain();
    }
  }
  draw() {
    if (this.disposed || !this.size) return;
    const { cols, rows, cw, ch } = this.size;
    const ratio = Math.min(devicePixelRatio || 1, 4);
    const width = cols * cw;
    const height = rows * ch;
    if (width * height * ratio * ratio > 33554432) { this.report(new Error("Image canvas exceeds 32 megapixels")); return; }
    this.canvas.width = Math.ceil(width * ratio);
    this.canvas.height = Math.ceil(height * ratio);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    const context = this.canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    for (const placement of [...(this.scene?.placements || [])].sort((left, right) => left.z - right.z)) {
      const image = this.cache.get(placement.key)?.bitmap;
      if (!image || placement.sw === 0 || placement.sh === 0) continue;
      if (placement.sx + placement.sw > image.width || placement.sy + placement.sh > image.height) continue;
      context.drawImage(image, placement.sx, placement.sy, placement.sw, placement.sh,
        placement.x * cw + placement.ox, placement.y * ch + placement.oy, placement.cols * cw, placement.rows * ch);
    }
  }
  reset() {
    this.generation += 1;
    for (const image of this.cache.values()) image.bitmap.close();
    this.cache.clear(); this.encoded.clear(); this.scene = null;
    if (!this.disposed) this.draw();
  }
  dispose() { if (this.disposed) return; this.disposed = true; this.reset(); this.canvas.width = 0; this.canvas.height = 0; }
}
