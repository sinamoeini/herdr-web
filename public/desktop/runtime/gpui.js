const handlers = new WeakMap();
const descriptors = new WeakMap();
const pressedNodes = new Set();
let pressedListeners = false;
const units = value => typeof value === "number" ? `${value}px` : String(value);
let activeView = null;
let activeEvent = null;
let lastPointer = { x:20, y:40 };
export const currentView = () => activeView;
export const pointer = () => lastPointer;
export function inView(view, callback) { const previous = activeView; activeView = view; try { return callback(); } finally { activeView = previous; } }
export const assetURL = value => /^(\/|https?:|data:|blob:)/.test(value) ? value : `/desktop/${value}`;
function normalized(event) {
  return new Proxy(event, { get(target, key) {
    if (key === "key_char") return typeof target.key === "string" && Array.from(target.key).length === 1 ? target.key : null;
    if (key === "key") return ({ ArrowDown:"down", ArrowUp:"up", ArrowLeft:"left", ArrowRight:"right", Escape:"escape", Enter:"enter", Backspace:"backspace", Tab:"tab", " ":"space" })[target.key] || target.key;
    if (key === "click_count") return target.detail;
    if (key === "position") return { x:target.clientX, y:target.clientY };
    if (key === "bounds") { const rect=target.currentTarget?.getBoundingClientRect(); return rect?{x:rect.x,y:rect.y,width:rect.width,height:rect.height}:null; }
    if(key==="local_position"){const rect=target.currentTarget?.getBoundingClientRect();return {x:target.clientX-(rect?.left||0),y:target.clientY-(rect?.top||0)};}
    if (key === "delta") return { x:target.deltaX, y:target.deltaY };
    if (key === "modifiers") return { control:target.ctrlKey, shift:target.shiftKey, alt:target.altKey, platform:target.metaKey };
    const value = Reflect.get(target, key, target); return typeof value === "function" ? value.bind(target) : value;
  } });
}

export function contextAnchor(event, target = event.currentTarget) {
  if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY) && (event.clientX !== 0 || event.clientY !== 0)) {
    return { x: event.clientX, y: event.clientY };
  }
  const bounds = target.getBoundingClientRect();
  return { x: bounds.left + Math.min(bounds.width / 2, 24), y: bounds.bottom };
}

export const MouseButton = Object.freeze({ Left: "left", Right: "right", Middle: "middle" });

export class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.key = null;
    this.styles = {};
    this.attributes = {};
    this.events = {};
    this.items = [];
    this.owner = activeView;
  }
  id(value) { this.key = String(value); this.attributes.id = String(value); return this; }
  attr(name, value) {
    if (/^on/i.test(name) || ["srcdoc", "innerHTML"].includes(name)) throw new Error("Executable attributes are not supported");
    this.attributes[name] = String(value); return this;
  }
  style(name, value) { this.styles[name] = value; return this; }
  child(value) { if (value !== null && value !== undefined && value !== false) this.items.push(value); return this; }
  children(values) { for (const value of values) this.child(value); return this; }
  when(condition, callback) { return condition ? callback(this) ?? this : this; }
  map(callback) { return callback(this) ?? this; }
  owned_content(){this.ownsChildren=true;return this;}
  flex() { return this.style("display", "flex"); }
  flex_row() { return this.flex().style("flexDirection", "row"); }
  flex_col() { return this.flex().style("flexDirection", "column"); }
  flex_1() { return this.style("flex", "1 1 0").style("minWidth", "0").style("minHeight", "0"); }
  items_center() { return this.style("alignItems", "center"); }
  justify_between() { return this.style("justifyContent", "space-between"); }
  p(value) { return this.style("padding", units(value)); }
  px(value) { return this.style("paddingInline", units(value)); }
  py(value) { return this.style("paddingBlock", units(value)); }
  gap(value) { return this.style("gap", units(value)); }
  w(value) { return this.style("width", units(value)); }
  h(value) { return this.style("height", units(value)); }
  w_full() { return this.w("100%"); }
  h_full() { return this.h("100%"); }
  bg(value) { return this.style("background", value); }
  text_color(value) { return this.style("color", value); }
  text_size(value) { return this.style("fontSize", units(value)); }
  rounded(value) { return this.style("borderRadius", units(value)); }
  overflow_y_scroll() { return this.style("overflowY", "auto"); }
  cursor_pointer() { return this.style("cursor", "pointer"); }
  on(event, callback) { this.events[event] = callback; return this; }
  on_click(callback) { return this.on("click", callback); }
  on_key_down(callback) { return this.on("keydown", callback); }
  on_mouse_down(button, callback) {
    if (button === MouseButton.Right || button === 2) {
      return this.on("contextmenu", event => {
        event.preventDefault();
        event.webAnchor = contextAnchor(event);
        lastPointer = event.webAnchor;
        callback(event, this.owner?.cx);
      });
    }
    const expected = button === MouseButton.Middle || button === 1 ? 1 : 0;
    return this.on("mousedown", event => { if (event.button === expected) callback(event, this.owner?.cx); });
  }
  on_mouse_up(button, callback) { const expected=button==="right"||button===2?2:button==="middle"||button===1?1:0;return this.on("mouseup", event => { if (event.button === expected) callback(event, this.owner?.cx); }); }
  on_mouse_move(callback) { return this.on("mousemove", callback); }
  on_scroll_wheel(callback) { return this.on("wheel", callback); }
  on_drop(callback) { this.on("dragover", event => event.preventDefault()); return this.on("drop", callback); }
  on_hover(callback) { this.on("mouseenter", (_event,cx) => callback(true,cx)); return this.on("mouseleave", (_event,cx) => callback(false,cx)); }
  hover(callback) { this.hoverStyle = callback(new Element())?.styles || {}; return this; }
  active(callback) { this.activeStyle = callback(new Element())?.styles || {}; return this; }
  track_focus(handle) { this.focusHandle = handle; return this.attr("tabindex", "-1"); }
  key_context(value) { return this.attr("data-key-context", value); }
  on_action(name, callback) { return this.on("keydown", (event,cx) => { if (this.owner?.bindings?.some(binding => binding.action === name && binding.keystroke === event.key)) {event.preventDefault();event.stopPropagation();callback(event,cx);} }); }
  on_mouse_down_out(button, callback) { this.outside = {button:typeof button==="function"?"left":button,callback:typeof button==="function"?button:callback}; return this; }
  on_size_change(callback) { this.sizeCallback = callback; return this; }
  size(value) { return this.w(value).h(value); }
  size_full() { return this.w_full().h_full(); }
  border(value) { return this.style("borderWidth", units(value)).style("borderStyle", "solid"); }
  border_1() { return this.border(1); }
  border_2() { return this.border(2); }
  mode(value) { return this.style("scrollbarGutter",value==="always"?"stable":"auto"); }
  frames(values,delay) { return this.on("webmount",node=>{if(node.webFrames)return;node.webFrames=true;let index=0;this.owner?.interval(()=>{if(node.isConnected)node.textContent=values[++index%values.length];},delay);}); }
  loop(property,options){if(!["opacity","top"].includes(property))throw new Error(`Unsupported animation: ${property}`);return this.on("webmount",node=>{if(node.webAnimation)return;node.webAnimation=node.animate([{[property]:property==="top"?`${options.from}px`:options.from},{[property]:property==="top"?`${options.to}px`:options.to}],{duration:options.period,iterations:Infinity,direction:"alternate"});this.owner?.own(()=>node.webAnimation.cancel());});}
  border_color(value) { return this.style("borderColor", value); }
  truncate() { return this.style("overflow", "hidden").style("textOverflow", "ellipsis").style("whiteSpace", "nowrap"); }
  rounded_full() { return this.rounded(9999); }
  spin(milliseconds) { return this.style("animation", `web-spin ${milliseconds}ms linear infinite`); }
  shadow_sm() { return this.style("boxShadow", "0 2px 8px #0006"); }
  tooltip(value) { return this.attr("title", typeof value === "string" ? value : String(value?.text || "")); }
}

for (const [name,property] of Object.entries({ min_w:"minWidth", min_h:"minHeight", max_w:"maxWidth", max_h:"maxHeight", pt:"paddingTop", pb:"paddingBottom", pl:"paddingLeft", pr:"paddingRight", mt:"marginTop", mb:"marginBottom", ml:"marginLeft", mr:"marginRight", m:"margin", top:"top", left:"left", right:"right", bottom:"bottom", rounded_bl:"borderBottomLeftRadius", border_b:"borderBottomWidth", border_t:"borderTopWidth", border_l:"borderLeftWidth", border_r:"borderRightWidth" })) Element.prototype[name] = function(value) { if (name.startsWith("border")) this.style("borderStyle", "solid"); return this.style(property,units(value)); };
for (const axis of ["x","y"]) Element.prototype[`m${axis}`] = function(value) { return this.style(axis === "x" ? "marginInline" : "marginBlock",units(value)); };
for (const [name,property] of Object.entries({font_family:"fontFamily",font_weight:"fontWeight",line_height:"lineHeight",opacity:"opacity",flex_shrink:"flexShrink",z_index:"zIndex"})) Element.prototype[name] = function(value) { return this.style(property,String(value)); };
for (const [name,property,value] of [
  ["flex_none","flex","0 0 auto"],["flex_wrap","flexWrap","wrap"],["items_start","alignItems","flex-start"],["items_end","alignItems","flex-end"],["items_stretch","alignItems","stretch"],
  ["justify_center","justifyContent","center"],["justify_end","justifyContent","flex-end"],["self_start","alignSelf","flex-start"],
  ["overflow_hidden","overflow","hidden"],["overflow_y_scrollbar","overflowY","auto"],["overflow_x_scrollbar","overflowX","auto"],
  ["relative","position","relative"],["absolute","position","absolute"],["inset_0","inset","0"],
  ["font_medium","fontWeight","500"],["font_semibold","fontWeight","600"],["font_bold","fontWeight","700"],
  ["text_center","textAlign","center"],["text_right","textAlign","right"],["whitespace_nowrap","whiteSpace","nowrap"],["underline","textDecoration","underline"],
  ["cursor_text","cursor","text"],["cursor_grab","cursor","grab"],
]) Element.prototype[name] = function() { return this.style(property,value); };

export const div = () => new Element("div");
export const button = () => new Element("button").attr("type", "button");
export const text = value => new Element("span").child(String(value));
export const img = source => new Element("img").attr("src",assetURL(source));
export const image = img;
export const svg = source => div().style("display","inline-block").style("backgroundColor","currentColor").style("mask",`url("${assetURL(source)}") center / contain no-repeat`).style("WebkitMask",`url("${assetURL(source)}") center / contain no-repeat`);
export const Background = {linear_gradient:(angle,from,to)=>`linear-gradient(${angle}deg,${from},${to})`,checkerboard:(color,size)=>`repeating-conic-gradient(${color} 0% 25%,transparent 0% 50%) 0 / ${size*2}px ${size*2}px`};
export class PathBuilder { static fill(){return new PathBuilder();} move_to(x,y){this.points=[[x,y]];return this;} line_to(x,y){this.points.push([x,y]);return this;} close(){return this;} build(){return this.points;} }

function interactiveStyles(node, descriptor) {
  if (!descriptor) return;
  const keys = new Set([...Object.keys(descriptor.hoverStyle || {}), ...Object.keys(descriptor.activeStyle || {})]);
  for (const key of keys) node.style[key] = (pressedNodes.has(node) ? descriptor.activeStyle?.[key] : undefined)
    ?? (node.webHovered ? descriptor.hoverStyle?.[key] : undefined) ?? descriptor.styles[key] ?? "";
}

function releasePressed(owner) {
  for (const node of pressedNodes) {
    const descriptor = descriptors.get(node);
    if (owner && descriptor?.owner !== owner) continue;
    pressedNodes.delete(node);
    interactiveStyles(node, descriptor);
  }
}

function updateEvents(node, descriptor) {
  const events = descriptor.events;
  const previous = handlers.get(node) || {};
  for (const [name, callback] of Object.entries(previous)) node.removeEventListener(name, callback);
  const wrapped = {};
  for (const [name,callback] of Object.entries(events)) {
    if (name === "webmount") continue;
    wrapped[name] = event => {
      if (descriptor.owner?.disposed) return;
      const previousEvent = activeEvent;
      activeEvent = event;
      if (event.clientX || event.clientY) lastPointer = {x:event.clientX,y:event.clientY};
      try { inView(descriptor.owner,()=>callback(normalized(event),descriptor.owner?.cx)); }
      catch(error) { globalThis.dispatchEvent(new CustomEvent("herdr-error",{detail:error})); }
      finally { activeEvent=previousEvent; }
    };
    node.addEventListener(name,wrapped[name]);
  }
  if(Object.keys(descriptor.hoverStyle||{}).length || Object.keys(descriptor.activeStyle||{}).length){
    for(const [name,enter] of [["mouseenter",true],["mouseleave",false]]){
      const original=wrapped[name];
      if(original)node.removeEventListener(name,original);
      wrapped[name]=event=>{ node.webHovered=enter; interactiveStyles(node,descriptors.get(node)); original?.(event); };
      node.addEventListener(name,wrapped[name]);
    }
  }
  if(Object.keys(descriptor.activeStyle||{}).length){
    const original=wrapped.pointerdown;
    if(original)node.removeEventListener("pointerdown",original);
    wrapped.pointerdown=event=>{if(event.button===0&&!descriptor.owner?.disposed){pressedNodes.add(node);interactiveStyles(node,descriptors.get(node));}original?.(event);};
    node.addEventListener("pointerdown",wrapped.pointerdown);
    if(!pressedListeners){pressedListeners=true;document.addEventListener("pointerup",()=>releasePressed(),true);document.addEventListener("pointercancel",()=>releasePressed(),true);globalThis.addEventListener("blur",()=>releasePressed());}
  }
  interactiveStyles(node,descriptor);
  handlers.set(node,wrapped);
}

function patch(parent, node, descriptor) {
  if (!(descriptor instanceof Element)) {
    const value = String(descriptor);
    if (!node || node.nodeType !== Node.TEXT_NODE) {
      const replacement = document.createTextNode(value);
      if (node) node.replaceWith(replacement); else parent.append(replacement);
      return replacement;
    }
    if (node.data !== value) node.data = value;
    return node;
  }
  const previous = node && descriptors.get(node);
  if (!node || node.nodeType !== Node.ELEMENT_NODE || node.localName !== descriptor.tag || (previous && previous.key !== descriptor.key)) {
    const replacement = document.createElement(descriptor.tag);
    if (node) node.replaceWith(replacement); else parent.append(replacement);
    node = replacement;
  }
  for (const name of Object.keys({...previous?.styles,...previous?.hoverStyle,...previous?.activeStyle})) if (!(name in descriptor.styles)) node.style[name] = "";
  Object.assign(node.style, descriptor.styles);
  for (const name of Object.keys(previous?.attributes || {})) if (!(name in descriptor.attributes)) node.removeAttribute(name);
  for (const [name, value] of Object.entries(descriptor.attributes)) {
    if (name === "value" && node instanceof HTMLInputElement) {
      if (document.activeElement !== node && node.value !== value) node.value = value;
    } else if (node.getAttribute(name) !== value) node.setAttribute(name, value);
  }
  updateEvents(node, descriptor);
  descriptors.set(node, descriptor);
  if(descriptor.ownsChildren){descriptor.events.webmount?.(node);return node;}
  const old = Array.from(node.childNodes);
  const keyed = new Map(old.map(child => [descriptors.get(child)?.key, child]).filter(([key]) => key !== null && key !== undefined));
  const used = new Set();
  let index = 0;
  for (const item of descriptor.items) {
    const key = item instanceof Element ? item.key : null;
    let candidate = key === null ? old.find(child => !used.has(child) && !descriptors.get(child)?.key) : keyed.get(key);
    if (candidate && used.has(candidate)) candidate = undefined;
    if(candidate)used.add(candidate);
    const child = patch(node, candidate, item);
    used.add(child);
    if (child !== node.childNodes[index]) node.insertBefore(child, node.childNodes[index] || null);
    index += 1;
  }
  for (const child of Array.from(node.childNodes)) if (!used.has(child)) child.remove();
  descriptor.events.webmount?.(node);
  if(descriptor.focusHandle){descriptor.focusHandle.node=node;if(descriptor.focusHandle.wanted){descriptor.focusHandle.wanted=false;queueMicrotask(()=>node.isConnected&&!node.contains(document.activeElement)&&node.focus());}}
  if(descriptor.sizeCallback&&!node.webSizeObserver){
    node.webSizeObserver=new ResizeObserver(entries=>{const current=descriptors.get(node),rect=entries[0].contentRect;if(!current?.owner?.disposed)inView(current.owner,()=>current.sizeCallback?.({width:rect.width,height:rect.height},current.owner?.cx));});
    node.webSizeObserver.observe(node);descriptor.owner?.own(()=>node.webSizeObserver.disconnect());
  }
  if(descriptor.outside&&!node.webOutside){node.webOutside=event=>{const current=descriptors.get(node),overlay=node.closest("[data-overlay]")?.dataset.overlay,parents=event.target.closest?.("[data-overlay]")?.dataset.overlayParents?.split(" ")||[];if(node.isConnected&&!node.contains(event.target)&&!parents.includes(overlay)&&event.button===0&&!current?.owner?.disposed)inView(current.owner,()=>current.outside?.callback(normalized(event),current.owner?.cx));};document.addEventListener("mousedown",node.webOutside);descriptor.owner?.own(()=>document.removeEventListener("mousedown",node.webOutside));}
  return node;
}

export class View {
  constructor(properties = {}) {
    this.webProps = Object.freeze({ ...properties });
    this.disposed = false;
    this.cleanups = new Set();
    this.abortController = new AbortController();
    this.webPendingRender = false;
    this.cx={
      notify:()=>this.notify(),stop_propagation:()=>{if(activeEvent?.type?.startsWith("key"))activeEvent.preventDefault();activeEvent?.stopPropagation();},
      focus_handle:()=>({node:null,wanted:false,focus(){if(this.node?.isConnected)this.node.focus();else this.wanted=true;}}),
      spawn:callback=>{const promise=inView(this,()=>callback(this.cx));Promise.resolve(promise).catch(error=>{if(!this.disposed&&error.name!=="AbortError")globalThis.dispatchEvent(new CustomEvent("herdr-error",{detail:error}));});return promise;},
      bind_keys:bindings=>{this.bindings=bindings;},
      timer:{every:(delay,callback)=>({cancel:this.interval(()=>inView(this,()=>callback(this.cx)),delay)}),after:(delay,callback)=>{const timer=setTimeout(()=>{if(!this.disposed)inView(this,()=>callback(this.cx));},delay);return {cancel:this.own(()=>clearTimeout(timer))};}},
      window:{write_to_clipboard:text=>navigator.clipboard.writeText(String(text)),paint_path:(_path,background)=>div().bg(background)},
    };
  }
  own(cleanup) { if (this.disposed) cleanup(); else this.cleanups.add(cleanup); return cleanup; }
  listen(target, event, callback, options) {
    target.addEventListener(event, callback, options);
    return this.own(() => target.removeEventListener(event, callback, options));
  }
  interval(callback, delay) {
    const timer = setInterval(() => { if (!this.disposed) callback(); }, delay);
    return this.own(() => clearInterval(timer));
  }
  async task(callback) {
    const result = await callback(this.abortController.signal);
    if (this.disposed) return undefined;
    return result;
  }
  mount(container) {
    if (this.disposed) throw new Error("Cannot mount a disposed view");
    if (this.webContainer && this.webContainer !== container) throw new Error("View already mounted elsewhere");
    this.webContainer = container;
    if(!this.webInitialized){this.webInitialized=true;inView(this,()=>this.init?.(this.webProps,this.cx));}
    this.webRoot = inView(this,()=>patch(container,this.webRoot,this.render(this.cx)));
    return this;
  }
  notify() {
    if (this.disposed || this.webPendingRender || !this.webContainer) return;
    this.webPendingRender = true;
    requestAnimationFrame(() => {
      this.webPendingRender = false;
      if(!this.disposed){try{this.webRoot=inView(this,()=>patch(this.webContainer,this.webRoot,this.render(this.cx)));}catch(error){globalThis.dispatchEvent(new CustomEvent("herdr-error",{detail:error}));}}
    });
  }
  dispose() {
    if (this.disposed) return;
    releasePressed(this);
    this.disposed = true;
    this.watched=null;
    this.watching?.clear?.();
    this.abortController.abort();
    for (const cleanup of this.cleanups) { try { cleanup(); } catch (error) { console.error(error); } }
    this.cleanups.clear();
    this.webRoot?.remove();
    this.webRoot = null;
    this.webContainer = null;
  }
}

let activePopup = null;

export function forwardPopupContext(event, popup) {
  if (!popup?.contains(event.target)) return;
  event.preventDefault();
  const hidden = [...document.querySelectorAll(".web-popup")].map(element=>[element,element.style.pointerEvents]);
  for(const [element] of hidden)element.style.pointerEvents="none";
  let target;
  try { target = document.elementFromPoint(event.clientX, event.clientY); }
  finally { for(const [element,previous] of hidden)element.style.pointerEvents=previous; }
  while (target && !handlers.get(target)?.contextmenu) target = target.parentElement;
  if (!target || popup.contains(target)) return;
  event.stopPropagation();
  target.dispatchEvent(new MouseEvent("contextmenu", { bubbles:true, cancelable:true, button:2, clientX:event.clientX, clientY:event.clientY }));
}

export class Popup {
  constructor() {
    this.element = null;
    this.outside = event => { if (event.button === 0 && !this.element?.contains(event.target)) this.close(); };
    this.keyboard = event => { if (event.key === "Escape") this.close(); };
    this.context = event => forwardPopupContext(event, this.element);
    document.addEventListener("pointerdown", this.outside);
    document.addEventListener("keydown", this.keyboard);
    document.addEventListener("contextmenu", this.context, true);
  }
  show(event, items) {
    activePopup?.close();
    this.close();
    activePopup = this;
    const target = event.currentTarget;
    const anchor = event.webAnchor || contextAnchor(event, target);
    const popup = document.createElement("div");
    popup.className = "web-popup";
    popup.setAttribute("role", "menu");
    for (const item of items) {
      const control = document.createElement("button");
      control.type = "button";
      control.textContent = item.label;
      control.setAttribute("role", "menuitem");
      control.addEventListener("click", () => { this.close(); item.action(); });
      popup.append(control);
    }
    this.element = popup;
    document.body.append(popup);
    popup.style.left = `${Math.max(8, Math.min(anchor.x, innerWidth - popup.offsetWidth - 8))}px`;
    popup.style.top = `${Math.max(8, Math.min(anchor.y, innerHeight - popup.offsetHeight - 8))}px`;
  }
  close() { this.element?.remove(); this.element = null; if (activePopup === this) activePopup = null; }
  dispose() {
    this.close();
    document.removeEventListener("pointerdown", this.outside);
    document.removeEventListener("keydown", this.keyboard);
    document.removeEventListener("contextmenu", this.context, true);
  }
}
