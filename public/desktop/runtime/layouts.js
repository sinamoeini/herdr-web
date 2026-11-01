const sizes = new Set(["width", "height", "minWidth", "minHeight", "weight", "gap", "padding"]);
const fields = new Set(["type", "id", "children", "content", "visible", "hidden", "size", "min", "max", "background", "resizable", "window_drag", "active", "floating", "edge", "align", "titlebar", "traffic_lights", "terminal_padding", "divider", "theme", "font", "ui_font", "font_size", "terminal_opacity", "colors", "preview", ...sizes]);

export function validateLayout(layout) {
  let count = 0;
  let terminals = 0;
  const ids = new Set();
  const numeric=(value,name,min=0,max=10000)=>{if(typeof value!=="number"||!Number.isFinite(value)||value<min||value>max)throw new Error(`${name} is out of range`);};
  const color=(value,name)=>{if(typeof value!=="string"||!/^([a-z][a-z0-9_-]*|#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8}))$/i.test(value))throw new Error(`Invalid ${name}`);};
  const record=(value,allowed,name)=>{if(!value||typeof value!=="object"||Array.isArray(value)||Object.keys(value).some(key=>!allowed.includes(key)))throw new Error(`Invalid ${name}`);};
  function visit(node, depth, hidden = false) {
    if (++count > 200 || depth > 20) throw new Error("Layout exceeds 200 nodes or 20 levels");
    if (!node || typeof node !== "object" || Array.isArray(node)) throw new Error("Layout node must be an object");
    for (const field of Object.keys(node)) if (!fields.has(field)) throw new Error(`Unsupported layout field: ${field}`);
    if(node.id!==undefined){if(typeof node.id!=="string"||!node.id||node.id.length>128||ids.has(node.id))throw new Error("Layout IDs must be unique nonempty strings");ids.add(node.id);}
    if (!["row", "column", "stack", "block"].includes(node.type)) throw new Error("Unsupported layout node type");
    for(const field of ["window_drag","resizable"])if(node[field]!==undefined&&typeof node[field]!=="boolean")throw new Error(`${field} must be Boolean`);
    for(const field of ["min","max","font_size"])if(node[field]!==undefined)numeric(node[field],field);
    if(node.terminal_opacity!==undefined)numeric(node.terminal_opacity,"terminal_opacity",0,1);
    if(node.background!==undefined)color(node.background,"background");
    if(node.colors!==undefined){if(!node.colors||typeof node.colors!=="object"||Array.isArray(node.colors))throw new Error("colors must be an object");for(const [name,value] of Object.entries(node.colors))color(value,name);}
    for(const field of ["font","ui_font","theme","preview","titlebar","edge","align"])if(node[field]!==undefined&&(typeof node[field]!=="string"||node[field].length>512))throw new Error(`Invalid ${field}`);
    if(node.edge!==undefined&&!["left","right","top","bottom"].includes(node.edge))throw new Error("Invalid floating edge");
    if(node.align!==undefined&&!["start","center","end"].includes(node.align))throw new Error("Invalid floating alignment");
    for(const field of ["traffic_lights","terminal_padding"])if(node[field]!==undefined){if(!Array.isArray(node[field])||node[field].length!==2)throw new Error(`Invalid ${field}`);node[field].forEach(value=>numeric(value,field));}
    if(node.divider!==undefined){record(node.divider,["width","color"],"divider");numeric(node.divider.width,"divider width",0,100);color(node.divider.color,"divider color");}
    if (node.visible !== undefined && typeof node.visible !== "boolean") throw new Error("visible must be Boolean");
    if(node.hidden!==undefined&&typeof node.hidden!=="boolean")throw new Error("hidden must be Boolean");
    hidden = hidden || node.visible === false || node.hidden === true;
    if(node.size!==undefined){if(!node.size||typeof node.size!=="object"||Object.keys(node.size).some(key=>!["px","weight"].includes(key))||Object.values(node.size).some(value=>typeof value!=="number"||!Number.isFinite(value)||value<0||value>10000))throw new Error("Invalid layout size");}
    for (const field of sizes) {
      if(field==="padding"&&Array.isArray(node[field])){if(![2,4].includes(node[field].length))throw new Error("Invalid padding");node[field].forEach(value=>numeric(value,"padding"));continue;}
      if (node[field] !== undefined && (typeof node[field] !== "number" || !Number.isFinite(node[field]) || node[field] < 0 || node[field] > 10000)) {
        throw new Error(`${field} must be a finite number from 0 through 10000`);
      }
    }
    if (node.type === "block") {
      if (typeof node.id !== "string" || !node.id) throw new Error("Blocks require an ID");
      if (node.children !== undefined) throw new Error("Blocks cannot have children");
      const content = node.content;
      if (!content || typeof content !== "object" || Array.isArray(content) || Object.keys(content).some(key=>!["tab","mod","props","pane"].includes(key)) || (Object.hasOwn(content,"tab")===Object.hasOwn(content,"mod"))) throw new Error("Block must have exactly one content kind");
      if (Object.hasOwn(content, "tab")) {
        if (![true,"active"].includes(content.tab) || hidden) throw new Error("Terminal content must be active and visible");
        terminals += 1;
      } else if (Object.hasOwn(content, "mod")) {
        if (typeof content.mod !== "string" || !/^[A-Za-z0-9_-]{1,79}$/.test(content.mod)) throw new Error("Invalid panel name");
      } else throw new Error("Unsupported content kind");
      if(content.props!==undefined&&(!content.props||typeof content.props!=="object"||Array.isArray(content.props)||JSON.stringify(content.props).length>16384))throw new Error("Panel properties must be a bounded object");
      if(content.pane!==undefined){const pane=content.pane;record(pane,["gap","radius","background","lone","border","focus","header"],"pane appearance");for(const field of ["gap","radius"])if(pane[field]!==undefined)numeric(pane[field],field,0,200);if(pane.background!==undefined)color(pane.background,"pane background");if(pane.lone!==undefined&&typeof pane.lone!=="boolean")throw new Error("lone must be Boolean");for(const field of ["border","focus"])if(pane[field]!==undefined){record(pane[field],["width","color"],field);numeric(pane[field].width,field,0,100);color(pane[field].color,field);}if(pane.header!==undefined){record(pane.header,["content","height","position","reveal"],"pane header");numeric(pane.header.height,"header height",1,200);if(pane.header.position!==undefined&&!["top","bottom"].includes(pane.header.position))throw new Error("Invalid header position");if(pane.header.reveal!==undefined&&!["always","hover","focus"].includes(pane.header.reveal))throw new Error("Invalid header reveal");record(pane.header.content,["mod","props"],"header content");if(typeof pane.header.content.mod!=="string"||!/^[A-Za-z0-9_-]{1,79}$/.test(pane.header.content.mod))throw new Error("Invalid header panel");}}
    } else {
      if (node.content !== undefined) throw new Error("Only blocks have content");
      if (!Array.isArray(node.children) || !node.children.length) throw new Error("Container requires children");
      const active=node.type==="stack"?(node.active??node.children[0].id):null;
      if(node.type==="stack"&&!node.children.some(child=>child.id===active))throw new Error("Stack active child does not exist");
      for (const child of node.children) visit(child, depth + 1, hidden||(node.type==="stack"&&child.id!==active));
    }
    if(node.floating!==undefined){if(!Array.isArray(node.floating))throw new Error("floating must be an array");for(const floating of node.floating)visit({...floating,type:floating.type||"block"},depth+1,hidden);}
  }
  visit(layout, 1);
  if (terminals !== 1) throw new Error("Layout must contain exactly one terminal block");
  return JSON.parse(JSON.stringify(layout));
}

export class PersonalLayouts {
  constructor(storage) { this.storage = storage; this.key = "herdr-web.layouts.v1"; }
  all() {
    try {
      const value = JSON.parse(this.storage.getItem(this.key) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch { return {}; }
  }
  save(name, layout) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) throw new Error("Layout name must contain 1–64 letters, digits, underscores, or hyphens");
    const valid = validateLayout(layout);
    const layouts = this.all();
    Object.defineProperty(layouts, name, { value: valid, enumerable: true, writable: true, configurable: true });
    this.storage.setItem(this.key, JSON.stringify(layouts));
    return valid;
  }
  remove(name) { const layouts = this.all(); delete layouts[name]; this.storage.setItem(this.key, JSON.stringify(layouts)); }
}
