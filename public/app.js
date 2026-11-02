import * as desktop from "./desktop/runtime/herdr.js";
import { pointer, forwardPopupContext } from "./desktop/runtime/gpui.js";
import { validateLayout } from "./desktop/runtime/layouts.js";
import { ImageScenes } from "./native-images.js";

const { model,request,run,theme,settings }=desktop;
const application=document.getElementById("app"), overlayRoot=document.getElementById("overlays"), notifications=document.getElementById("notifications");
const blocks=new Map(), panels=new Map(), terminalViews=new Map(), overlays=new Map();
let overlayNumber=0, popup=null, eventSocket=null, eventGeneration=0, paneSubscription="", currentLayout="", stopped=false;
const terminalContent=document.createElement("div");terminalContent.className="terminal-content";
const modules=new Map();
const moduleURL=name=>name.startsWith("frame/")?`/desktop/${name}/main.js`:model.custom?.includes(name)?`/user-mods/${name}/main.js`:`/desktop/mods/${name}/main.js`;
async function panel(key,name,container,properties={}){
  let record=panels.get(key);
  if(record&&record.name!==name){record.view?.dispose();panels.delete(key);record=null;}
  if(!record){
    record={name,container,properties};panels.set(key,record);
    try{
      if(!modules.has(name))modules.set(name,import(moduleURL(name)));
      const module=await modules.get(name);
      if(panels.get(key)!==record)return;
      record.view=new module.default(properties);record.view.mount(container);
    }catch(error){if(panels.get(key)===record){container.textContent=`${name}: ${error.message}`;notice(error.message);}}
  }else{record.properties=properties;if(record.view){record.view.webProps=Object.freeze({...properties});record.view.notify();}}
}
function discardPanel(key){const record=panels.get(key);if(record){record.view?.dispose();panels.delete(key);}}
function notice(message){if(!message)return;const toast=document.createElement("div");toast.className="web-toast";toast.textContent=String(message);notifications.append(toast);setTimeout(()=>toast.remove(),7000);}
globalThis.addEventListener("herdr-error",event=>notice(event.detail?.message||event.detail));
function tint(value){return theme()[value]||value||"transparent";}
function padding(value){return Array.isArray(value)?value.map(number=>`${number}px`).join(" "):`${value||0}px`;}
function renderLayout(){
  const seen=new Set(), activePanels=new Set();
  const walk=(node,parent,path="root",parentType="row",position=0)=>{
    const key=node.id||path;seen.add(key);
    let element=blocks.get(key);if(!element){element=document.createElement("div");element.dataset.block=key;element.className="layout-node";blocks.set(key,element);}
    if(parent.children[position]!==element)parent.insertBefore(element,parent.children[position]||null);
    const hidden=node.hidden===true||node.visible===false;
    element.hidden=hidden;
    element.style.cssText=`display:${hidden?"none":"flex"};flex-direction:${node.type==="row"?"row":"column"};min-width:0;min-height:0;position:relative;overflow:hidden;flex:${node.size?.px!==undefined?`0 0 ${node.size.px}px`:`${node.size?.weight||1} 1 0`};gap:${node.gap||0}px;padding:${padding(node.padding)};background:${tint(node.background)};`;
    if(node.min)element.style[parentType==="row"?"minWidth":"minHeight"]=`${node.min}px`;
    if(node.max)element.style[parentType==="row"?"maxWidth":"maxHeight"]=`${node.max}px`;
    for(const [key,property] of [["width","width"],["height","height"],["minWidth","minWidth"],["minHeight","minHeight"]])if(node[key]!==undefined)element.style[property]=`${node[key]}px`;
    if(node.weight!==undefined&&!node.size)element.style.flex=`${node.weight} 1 0`;
    const fixed=node[parentType==="row"?"width":"height"];if(fixed!==undefined&&!node.size)element.style.flex=`0 0 ${fixed}px`;
    if(hidden)return;
    if(node.type==="block"){
      if(node.content?.tab){if(terminalContent.parentNode!==element)element.append(terminalContent);model.paneAppearance=node.content.pane||{};}
      else if(node.content?.mod){activePanels.add(key);void panel(key,node.content.mod,element,node.content.props||{});}
    }else{
      let placed=0;
      for(const [index,child] of (node.children||[]).entries()){
        if(node.type==="stack"&&child.id!==(node.active||node.children[0]?.id))continue;
        walk(child,element,`${key}-${index}`,node.type,placed++);
      }
      for(const [index,floating] of (node.floating||[]).entries()){
        walk({...floating,type:floating.type||"block"},element,`${key}-floating-${index}`,node.type,placed++);
        const float=blocks.get(floating.id||`${key}-floating-${index}`);float.style.position="absolute";float.style.zIndex="20";float.style.width="200px";float.style.height="220px";float.style.bottom="0";float.style.left="0";
      }
    }
  };
  walk(model.layout,application);
  for(const [key,element] of blocks)if(!seen.has(key)){element.remove();blocks.delete(key);}
  for(const key of panels.keys())if(!key.startsWith("overlay-")&&!key.startsWith("pane-")&&!activePanels.has(key))discardPanel(key);
}
function websocket(path){return new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}${path}`);}
class PaneTerminal{
  constructor(pane,preview=false){
    this.pane=pane;this.preview=preview;this.generation=0;this.disposed=false;this.ready=false;
    this.element=document.createElement("section");this.element.className="terminal-pane";this.element.dataset.pane=pane.pane;
    this.header=document.createElement("div");this.header.className="pane-header";
    this.viewport=document.createElement("div");this.viewport.className="terminal-viewport";
    this.error=document.createElement("div");this.error.className="terminal-status";this.error.hidden=true;
    this.canvas=document.createElement("canvas");this.canvas.className="web-image-canvas";
    this.element.append(this.header,this.viewport,this.error);this.element.addEventListener("pointerdown",()=>{if(!preview)run("pane.focus",{pane:pane.pane});});
    this.element.addEventListener("contextmenu",event=>{if(event.shiftKey||!this.terminal?.modes.mouseTrackingMode||this.terminal.modes.mouseTrackingMode==="none"){event.preventDefault();openOverlay({mod:"menu",kind:"popup",width:220,props:{tab:"pane",target:{pane:pane.pane,terminal:pane.terminal}}},event);}});
    this.element.addEventListener("dragover",event=>{if(event.dataTransfer?.types.includes("Files"))event.preventDefault();});
    this.element.addEventListener("drop",event=>{const file=[...event.dataTransfer.files].find(file=>file.type.startsWith("image/"));if(file){event.preventDefault();void this.upload(file);}});
    this.viewport.addEventListener("paste",event=>{const image=[...event.clipboardData.items].find(item=>item.type.startsWith("image/"));if(image){event.preventDefault();event.stopPropagation();void this.upload(image.getAsFile());}},true);
    this.resizeObserver=new ResizeObserver(()=>this.fit());this.resizeObserver.observe(this.viewport);
    this.start();
  }
  async start(){
    await document.fonts.load(`${settings().font_size||14}px "${settings().font||"JetBrains Mono"}"`);await document.fonts.ready;if(this.disposed)return;
    this.terminal=new globalThis.Terminal({fontFamily:`"${settings().font||"JetBrains Mono"}","JetBrains Mono","Symbols Nerd Font",monospace`,fontSize:settings().font_size||14,allowProposedApi:false,scrollback:5000,convertEol:false,cursorBlink:true,theme:terminalTheme()});
    this.fitAddon=new globalThis.FitAddon.FitAddon();this.terminal.loadAddon(this.fitAddon);this.terminal.open(this.viewport);this.viewport.append(this.canvas);this.images=new ImageScenes(this.canvas,error=>notice(`Graphics: ${error.message}`));
    this.terminal.onData(data=>{if(this.ready&&this.socket?.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify({type:"input",data}));});
    this.terminal.attachCustomKeyEventHandler(event=>{if(desktop.reservedKey(event))return false;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="v")return false;if(event.ctrlKey&&event.shiftKey&&["p","c"].includes(event.key.toLowerCase()))return false;const dialog=[...document.querySelectorAll(".web-dialog")].at(-1);return !dialog||dialog.contains(this.element);});
    this.fit();this.connect();this.connectGraphics();
  }
  connect(){
    if(this.disposed)return;const generation=++this.generation;this.inputReady(false);this.sentResize="";this.socket?.close();
    const socket=this.socket=websocket(`/terminal/${encodeURIComponent(this.pane.pane)}`);socket.binaryType="arraybuffer";
    socket.onopen=()=>{if(generation===this.generation)this.fit();};
    socket.onmessage=event=>{if(this.disposed||generation!==this.generation)return;if(typeof event.data!=="string")this.terminal.write(new Uint8Array(event.data));else{const value=JSON.parse(event.data);if(value.ready){this.inputReady(true);this.error.hidden=true;this.fit();}if(value.error)this.showError(value.error);}};
    socket.onclose=event=>{if(this.disposed||generation!==this.generation)return;this.inputReady(false);this.showError(event.reason||"Attachment disconnected. Retrying…");if(event.code!==1000)this.retry=setTimeout(()=>this.connect(),1500);};
    socket.onerror=()=>this.showError("Terminal connection failed; retrying without taking over another client.");
  }
  inputReady(value){this.ready=value;this.element.dataset.ready=String(value);if(this.terminal)this.terminal.options.disableStdin=!value;if(value&&this.wantsFocus)this.focus();}
  focus(){this.wantsFocus=true;if(!this.ready||!this.terminal||this.disposed)return;this.wantsFocus=false;const overlay=[...document.querySelectorAll(".web-dialog,.web-popup")].at(-1);if(document.querySelector("dialog[open]")||(overlay&&!overlay.contains(this.element)))return;this.terminal.focus();}
  showError(text){this.error.hidden=false;this.error.replaceChildren(document.createTextNode(text+" "));const retry=document.createElement("button");retry.textContent="Retry";retry.onclick=()=>{clearTimeout(this.retry);this.connect();};this.error.append(retry);}
  appearance(pane){const appearance=model.paneAppearance,edge=(pane.focused?appearance.focus:appearance.border)||{};this.element.style.borderColor=tint(edge.color||(pane.focused?theme().accent:theme().border));this.element.style.borderWidth=`${edge.width??1}px`;this.viewport.style.padding=padding(model.layout.terminal_padding||[6,8]);this.header.style.order=appearance.header?.position==="bottom"?"2":"0";this.element.dataset.reveal=appearance.header?.reveal||"always";if(!this.terminal)return;this.terminal.options.theme=terminalTheme();const font=settings().font||"JetBrains Mono",size=settings().font_size||14,key=`${font}:${size}`;if(this.fontKey!==key){this.fontKey=key;document.fonts.load(`${size}px "${font}"`).then(()=>{if(!this.disposed&&this.fontKey===key){this.terminal.options.fontFamily=`"${font}","JetBrains Mono","Symbols Nerd Font",monospace`;this.terminal.options.fontSize=size;this.fit();}});}else this.fit();}
  connectGraphics(){if(this.disposed)return;const generation=this.graphicsGeneration=(this.graphicsGeneration||0)+1;this.graphics?.close();this.images?.reset();const socket=this.graphics=websocket(`/graphics/${encodeURIComponent(this.pane.pane)}`);socket.onopen=()=>{if(this.lastViewport)socket.send(JSON.stringify(this.lastViewport));};socket.onmessage=event=>{if(this.disposed||generation!==this.graphicsGeneration)return;try{const value=JSON.parse(event.data);if(value.error){if(this.graphicsError!==value.error)notice(value.error);this.graphicsError=value.error;}else{this.images.receive(event.data);this.graphicsError=null;}}catch(error){notice(error.message);}};socket.onclose=()=>{if(!this.disposed&&generation===this.graphicsGeneration)this.graphicsRetry=setTimeout(()=>this.connectGraphics(),1500);};}
  fit(){if(!this.terminal||this.viewport.clientWidth<10||this.viewport.clientHeight<10)return;try{this.fitAddon.fit();const cols=Math.min(500,this.terminal.cols),rows=Math.min(200,this.terminal.rows);if(cols!==this.terminal.cols||rows!==this.terminal.rows)this.terminal.resize(cols,rows);const screen=this.viewport.querySelector(".xterm-screen");const viewport={cols,rows,cw:Math.max(1,Math.min(100,(screen?.clientWidth||cols*8)/cols)),ch:Math.max(1,Math.min(200,(screen?.clientHeight||rows*16)/rows))};const key=JSON.stringify(viewport);if(this.socket?.readyState===1&&this.sentResize!==`${cols}:${rows}`){this.socket.send(JSON.stringify({type:"resize",cols,rows}));this.sentResize=`${cols}:${rows}`;}if(JSON.stringify(this.lastViewport)!==key){this.images?.viewport(viewport);if(this.graphics?.readyState===1)this.graphics.send(key);}if(screen){const bounds=screen.getBoundingClientRect(),parent=this.viewport.getBoundingClientRect();this.canvas.style.left=`${bounds.left-parent.left}px`;this.canvas.style.top=`${bounds.top-parent.top}px`;}this.lastViewport=viewport;}catch(error){notice(error.message);}}
  async upload(file){try{const result=await fetch("/api/clipboard-image",{method:"POST",headers:{"Content-Type":file.type},body:file});const value=await result.json();if(!result.ok)throw new Error(value.error);this.terminal.paste("'"+value.path.replaceAll("'","'\"'\"'")+"'");}catch(error){notice(error.message);}}
  dispose(){this.disposed=true;++this.generation;clearTimeout(this.retry);clearTimeout(this.graphicsRetry);this.socket?.close();this.graphics?.close();this.resizeObserver.disconnect();this.images?.dispose();this.terminal?.dispose();discardPanel(`pane-${this.pane.pane}-${this.preview}`);this.element.remove();}
}
function terminalTheme(){const colors=theme();const value={background:colors.background,foreground:colors.terminal_foreground,cursor:colors.cursor,selectionBackground:colors.selection};["black","red","green","yellow","blue","magenta","cyan","white","brightBlack","brightRed","brightGreen","brightYellow","brightBlue","brightMagenta","brightCyan","brightWhite"].forEach((key,index)=>value[key]=colors[`ansi${index}`]);return value;}
function renderTerminals(){
  const shown=desktop.panes().filter(pane=>pane.tab===model.selected.tab&&(!model.selected.zoom||pane.pane===model.selected.zoom));const ids=new Set(shown.map(pane=>pane.pane));
  for(const [id,view] of terminalViews)if(!ids.has(id)){view.dispose();terminalViews.delete(id);}
  for(const pane of shown){let view=terminalViews.get(pane.pane);if(!view){view=new PaneTerminal(pane);terminalViews.set(pane.pane,view);terminalContent.append(view.element);}view.pane=pane;view.element.classList.toggle("focused",pane.focused);view.element.style.borderRadius=`${model.paneAppearance.radius??10}px`;view.viewport.style.background=tint(model.paneAppearance.background||theme().background);view.header.style.height=`${model.paneAppearance.header?.height||30}px`;void panel(`pane-${pane.pane}-false`,model.paneAppearance.header?.content?.mod||"pane",view.header,{...model.paneAppearance.header?.content?.props,pane:pane.pane,terminal:pane.terminal});view.appearance(pane);}
  terminalContent.querySelectorAll(".split-divider,.empty-session").forEach(node=>node.remove());
  if(!shown.length){const empty=document.createElement("div");empty.className="empty-session";empty.textContent="No terminals yet. ";const create=document.createElement("button");create.textContent="Create space";create.onclick=()=>run("space.new");empty.append(create);terminalContent.append(empty);return;}
  const place=(node,rect,path=[])=>{
    if(node.type==="pane"){const view=terminalViews.get(node.pane_id);if(view){const gap=(model.paneAppearance.gap||8)/2;Object.assign(view.element.style,{left:`calc(${rect.x*100}% + ${gap}px)`,top:`calc(${rect.y*100}% + ${gap}px)`,width:`calc(${rect.width*100}% - ${gap*2}px)`,height:`calc(${rect.height*100}% - ${gap*2}px)`});}return;}
    const horizontal=node.direction==="right";const ratio=node.ratio||.5;
    place(node.first,{...rect,[horizontal?"width":"height"]:(horizontal?rect.width:rect.height)*ratio},[...path,false]);
    place(node.second,{...rect,[horizontal?"x":"y"]:(horizontal?rect.x:rect.y)+(horizontal?rect.width:rect.height)*ratio,[horizontal?"width":"height"]:(horizontal?rect.width:rect.height)*(1-ratio)},[...path,true]);
    const divider=document.createElement("div");divider.className=`split-divider ${horizontal?"vertical":"horizontal"}`;
    Object.assign(divider.style,{left:`${(rect.x+(horizontal?rect.width*ratio:0))*100}%`,top:`${(rect.y+(horizontal?0:rect.height*ratio))*100}%`,width:horizontal?"8px":`${rect.width*100}%`,height:horizontal?`${rect.height*100}%`:"8px"});
    divider.onpointerdown=event=>{event.preventDefault();const bounds=terminalContent.getBoundingClientRect();divider.setPointerCapture(event.pointerId);const move=event=>{const value=horizontal?((event.clientX-bounds.left)/bounds.width-rect.x)/rect.width:((event.clientY-bounds.top)/bounds.height-rect.y)/rect.height;node.ratio=Math.max(.1,Math.min(.9,value));};divider.onpointermove=move;divider.onpointerup=async()=>{divider.onpointermove=null;try{await request("/api/command",{method:"layout.set_split_ratio",params:{tab_id:model.selected.tab,path,ratio:node.ratio}});await refresh.request();}catch(error){notice(error.message);}};};terminalContent.append(divider);
  };
  if(model.selected.zoom)place({type:"pane",pane_id:model.selected.zoom},{x:0,y:0,width:1,height:1});
  else if(model.backendLayout?.tab_id===model.selected.tab)place(model.backendLayout.root,{x:0,y:0,width:1,height:1});
}
function render(){if(!model.layout)return;const colors=theme();for(const [key,value] of Object.entries(colors))document.documentElement.style.setProperty(`--${key.replaceAll("_","-")}`,value);document.body.style.fontFamily=`"${settings().ui_font||"Geist"}",sans-serif`;application.style.zoom=settings().zoom;renderLayout();renderTerminals();for(const [key,record] of panels)if(key.startsWith("overlay-"))record.view?.notify();}
function openOverlay(options,event){
  if(options.kind==="popup"){const parents=new Set(options.props?.parents||[]);for(const [id,overlay] of [...overlays])if(overlay.kind==="popup"&&!parents.has(id))closeOverlay(id);}
  else for(const [id,overlay] of [...overlays])if(overlay.kind==="popup")closeOverlay(id);
  const id=`overlay-${++overlayNumber}`;const container=document.createElement("div");container.className=options.kind==="popup"?"web-popup":"web-dialog";container.dataset.overlay=id;container.setAttribute("role",options.kind==="popup"?"menu":"dialog");container.style.width=`${Math.min(innerWidth-24,options.width||480)}px`;if(options.height)container.style.height=`${Math.min(innerHeight-24,options.height)}px`;
  if(options.kind==="window")container.classList.add("web-window");if(options.frame==="none")container.classList.add("web-frameless");
  container.dataset.overlayParents=(options.props?.parents||[]).join(" ");
  if(options.kind==="window"&&!options.width)container.style.width="max-content";
  const anchor=options.at||(event?{x:event.clientX,y:event.clientY}:pointer());
  if(options.kind==="popup"){popup=id;container.style.left=`${Math.max(8,Math.min(anchor.x,innerWidth-(options.width||250)-12))}px`;container.style.top=`${Math.max(8,Math.min(anchor.y,innerHeight-(options.height||240)-12))}px`;}
  else {container.style.left="50%";container.style.top="50%";container.style.transform="translate(-50%,-50%)";}
  const host=document.createElement("div");host.style.cssText="display:flex;flex-direction:column;min-height:0;min-width:0;flex:1";container.append(host);
  if(options.kind==="window"&&!options.height)host.style.flex="0 0 auto";
  const overlay={container,host,kind:options.kind,previous:document.activeElement,cleanups:new Set()};overlays.set(id,overlay);overlayRoot.append(container);
  if(options.kind==="window")container.addEventListener("pointerdown",event=>{const bounds=container.getBoundingClientRect();if(event.button!==0||event.clientY-bounds.top>30)return;const start={x:event.clientX,y:event.clientY};const move=event=>{if(Math.abs(event.clientX-start.x)+Math.abs(event.clientY-start.y)<4)return;container.style.transform="none";container.style.left=`${Math.max(0,Math.min(innerWidth-60,bounds.left+event.clientX-start.x))}px`;container.style.top=`${Math.max(0,Math.min(innerHeight-40,bounds.top+event.clientY-start.y))}px`;};const stop=()=>{globalThis.removeEventListener("pointermove",move);globalThis.removeEventListener("pointerup",stop);globalThis.removeEventListener("pointercancel",stop);overlay.cleanups.delete(stop);};overlay.cleanups.add(stop);globalThis.addEventListener("pointermove",move);globalThis.addEventListener("pointerup",stop,{once:true});globalThis.addEventListener("pointercancel",stop,{once:true});});
  void panel(id,options.mod,host,{...options.props,overlay:id});return id;
}
function closeOverlay(id){if(!id)id=[...overlays.keys()].at(-1);const overlay=overlays.get(id);if(!overlay)return;for(const cleanup of overlay.cleanups)cleanup();discardPanel(id);overlay.terminal?.dispose();overlay.container.remove();overlays.delete(id);if(popup===id)popup=[...overlays].filter(([,value])=>value.kind==="popup").at(-1)?.[0]||null;overlay.previous?.focus?.();}
document.addEventListener("pointerdown",event=>{if(event.button!==0||!popup||overlays.get(popup)?.container.contains(event.target))return;const menus=[...overlays].filter(([,value])=>value.kind==="popup");if(menus.some(([,value])=>value.container.contains(event.target)))closeOverlay(popup);else for(const [id] of menus)closeOverlay(id);});
document.addEventListener("contextmenu",event=>{const target=[...overlays.values()].find(value=>value.kind==="popup"&&value.container.contains(event.target));forwardPopupContext(event,target?.container);},true);
document.addEventListener("keydown",event=>{const terminal=!!event.target.closest?.(".terminal-pane");const action=desktop.keyCommand(event,terminal);if(!action||action==="terminal.paste_image")return;if(action!=="app.palette"&&(event.target.closest?.('dialog,input,textarea,[contenteditable="true"]')&&!terminal||overlays.size&&(!terminal||!action.startsWith("terminal.")&&!action.startsWith("send:"))))return;event.preventDefault();event.stopPropagation();desktop.dispatchKey(action);},true);
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!event.defaultPrevented&&overlays.size){closeOverlay();event.preventDefault();}});
function activeTerminal(){return [...overlays.values()].map(overlay=>overlay.terminal).find(view=>view?.element.contains(document.activeElement))||terminalViews.get(model.selected.pane);}
function copy(){const text=activeTerminal()?.terminal?.getSelection()||globalThis.getSelection()?.toString();if(text)navigator.clipboard.writeText(text).catch(error=>notice(error.message));}
async function paste(){try{activeTerminal()?.terminal?.paste(await navigator.clipboard.readText());}catch(error){notice(error.message);}}
async function preview(terminal){const pane=await desktop.resolveTerminalPane(terminal);const id=openOverlay({mod:"peek",kind:"dialog",width:850,height:500,props:{terminal}});const overlay=overlays.get(id);if(!overlay)return;overlay.host.style.flex="0 0 36px";const viewport=document.createElement("div");viewport.style.cssText="position:relative;flex:1;min-height:0";overlay.container.append(viewport);const view=new PaneTerminal({pane:pane.pane_id,terminal:pane.terminal_id},true);view.header.hidden=true;view.element.style.cssText="inset:0;width:100%;height:100%";viewport.append(view.element);overlay.terminal=view;}
function find(node,id){if(node.id===id)return node;for(const child of node.children||[]){const value=find(child,id);if(value)return value;}return null;}
function layoutCommand(name,args){if(name==="layout.set"&&args.layout){model.layout=validateLayout(args.layout);scheduleRender();return;}const node=find(model.layout,args.id||args.stack);if(!node)throw new Error("Layout target not found");if(name==="layout.show")node.hidden=false;else if(name==="layout.hide")node.hidden=true;else if(name==="layout.select")node.active=args.child;else if(name==="layout.resize")node.size={px:args.px||args.size||268};else if(name==="layout.set")Object.assign(node,args.props||args.value||{});else throw new Error(`Unsupported layout action ${name}`);scheduleRender();}
async function switchLayout(name,reset=false){if(!model.builtins[name]&&!model.personal[name])throw new Error("Unknown layout");model.layout=validateLayout(model.personal[name]||model.builtins[name]);currentLayout=name;model.shared.layout=name;localStorage.setItem("herdr-web.shared.v1",JSON.stringify(model.shared));for(const id of [...overlays.keys()])closeOverlay(id);render();}
function saveLayout(name,value){if(!/^[A-Za-z0-9_-]{1,64}$/.test(name)||Object.hasOwn(model.builtins,name))throw new Error("Choose a personal layout name (1–64 letters, digits, _ or -); built-ins are immutable");const valid=validateLayout(value);Object.defineProperty(model.personal,name,{value:valid,writable:true,enumerable:true,configurable:true});localStorage.setItem("herdr-web.personal.v1",JSON.stringify(model.personal));return switchLayout(name);}
function layoutEditor(){const box=document.createElement("dialog");box.className="layout-editor";const title=document.createElement("h2");title.textContent="Personal layout editor";const name=document.createElement("input");name.value=model.builtins[currentLayout]?`${currentLayout}-personal`:currentLayout;const text=document.createElement("textarea");text.value=JSON.stringify(model.layout,null,2);const error=document.createElement("p");box.append(title,name,text,error);for(const [label,action] of [["Save copy / switch",()=>saveLayout(name.value,JSON.parse(text.value))],["Download",()=>{const link=document.createElement("a");const url=URL.createObjectURL(new Blob([text.value],{type:"application/json"}));link.href=url;link.download=`${name.value}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}],["Delete personal layout",()=>{if(model.builtins[name.value])throw new Error("Built-ins are immutable");delete model.personal[name.value];localStorage.setItem("herdr-web.personal.v1",JSON.stringify(model.personal));switchLayout("main");}],["Cancel",()=>box.close()]]){const button=document.createElement("button");button.textContent=label;button.onclick=()=>{try{action();if(!label.startsWith("Download"))box.close();}catch(failure){error.textContent=failure.message;}};box.append(button);}box.onclose=()=>box.remove();document.body.append(box);box.showModal();}
const refresh=desktop.coalescedRefresh(async signal=>{
  const revision=model.selectionRevision,before=new Map(desktop.agents().map(agent=>[agent.address,agent.status]));const value=await request(`/api/state${model.selected.tab?`?tab=${encodeURIComponent(model.selected.tab)}`:""}`,undefined,{signal});if(revision!==model.selectionRevision){void refresh.request();return;}desktop.ingest(value);render();
  if(model.selected.tab&&value.layout?.tab_id!==model.selected.tab)void refresh.request();
  for(const agent of desktop.agents())if(before.has(agent.address)&&before.get(agent.address)!==agent.status&&["blocked","done"].includes(agent.status)){notice(`${agent.name}: ${agent.status==="blocked"?"needs you":"finished"}`);if(settings().sounds?.enabled&&navigator.userActivation?.hasBeenActive)desktop.playSound(agent.status);}
  const ids=value.snapshot.panes.map(pane=>pane.pane_id).sort().join(",");if(ids!==paneSubscription){paneSubscription=ids;connectEvents();}
},error=>{notice(error.message);document.getElementById("connection").textContent=error.message;});
function connectEvents(){const generation=++eventGeneration;eventSocket?.close();const socket=eventSocket=websocket("/api/events");socket.onopen=()=>document.getElementById("connection").textContent="";socket.onmessage=event=>{if(generation!==eventGeneration)return;const value=JSON.parse(event.data);if(value.type==="refresh")void refresh.request();if(value.error)notice(value.error);};socket.onclose=()=>{if(!stopped&&generation===eventGeneration)setTimeout(connectEvents,1500);};}
let renderPending=false;
function scheduleRender(){if(renderPending)return;renderPending=true;requestAnimationFrame(()=>{renderPending=false;render();});}
desktop.configure({render:scheduleRender,refresh:()=>refresh.request(),openOverlay,closeOverlay,notice,focus:id=>terminalViews.get(id)?.focus(),preview,copy,paste,send:data=>{const view=activeTerminal();if(view?.ready&&view.socket?.readyState===1)view.socket.send(JSON.stringify({type:"input",data}));},layoutCommand,switchLayout,saveLayout,layoutEditor});
document.getElementById("layout-editor-button").onclick=layoutEditor;
document.getElementById("new-space-button").onclick=()=>run("space.new");
document.getElementById("parked-button").onclick=()=>openOverlay({mod:"agents",kind:"dialog",width:450,height:520,props:{parked:true}});
globalThis.addEventListener("beforeunload",()=>{stopped=true;refresh.dispose();eventSocket?.close();for(const view of terminalViews.values())view.dispose();for(const record of panels.values())record.view?.dispose();});
try{
  await Promise.all([document.fonts.load('14px "JetBrains Mono"'),document.fonts.load('13px "Geist"')]);await document.fonts.ready;
  if(!localStorage.getItem("herdr-web.preferences.v1"))desktop.preferences.save({sounds:{enabled:false}});
  model.programs=await request("/desktop/programs.json");model.custom=await request("/api/mods");
  model.keymap=await request("/desktop/keys.json");
  Object.assign(model.palette,await request("/desktop/themes/palettes.json"));
  for(const name of ["main","terminals","win98"])model.builtins[name]=await request(`/desktop/layouts/${name}.json`);
  for(const name of ["atom-one-dark","win98"]){const source=await request(`/desktop/themes/${name}.json`);model.palette[name]=source.colors||source;}
  await switchLayout(desktop.shared("layout")||"main");await refresh.request();if(!eventSocket)connectEvents();setInterval(()=>refresh.request(),60000);
}catch(error){notice(error.message);document.getElementById("connection").textContent=error.message;}
