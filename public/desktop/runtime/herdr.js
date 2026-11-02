import { currentView, div } from "./gpui.js";
const preferenceKey = "herdr-web.preferences.v1";
const selectionKey = "herdr-web.selection.v1";

export async function request(path, body, options = {}) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

export async function resolveTerminalPane(terminal) {
  const value = await request("/api/state");
  const pane = value.snapshot.panes.find(candidate => candidate.terminal_id === terminal);
  if (!pane) throw new Error("Terminal no longer exists");
  return pane;
}

function stored(storage, key) {
  try { const value=JSON.parse(storage.getItem(key) || "{}");return value&&typeof value==="object"&&!Array.isArray(value)?value:{}; } catch { return {}; }
}
export const preferences = {
  read: () => stored(localStorage, preferenceKey),
  save: value => localStorage.setItem(preferenceKey, JSON.stringify(value)),
};
export const selection = {
  read: () => stored(sessionStorage, selectionKey),
  save: value => sessionStorage.setItem(selectionKey, JSON.stringify(value)),
};

export function coalescedRefresh(refresh, report = console.error) {
  let running = null;
  let pending = false;
  let disposed = false;
  const controller = new AbortController();
  return {
    request() {
      if (disposed) return Promise.resolve();
      pending = true;
      if (running) return running;
      running = Promise.resolve().then(async()=>{
        try {
          while (pending && !disposed) {
            pending = false;
            try { await refresh(controller.signal); }
            catch (error) { if (!disposed) report(error); }
          }
        } finally { running = null; }
      });
      return running;
    },
    dispose() { disposed = true; pending = false; controller.abort(); },
  };
}

export function watchFolder(path, changed, report = console.error) {
  if (typeof changed !== "function") {
    const owner=changed||currentView(),signal=owner?.abortController?.signal;
    return (async()=>{
      const until=owner?.webMissingWatches?.get(path)||0;
      if(until>Date.now())await new Promise((resolve,reject)=>{
        if(signal?.aborted){reject(new DOMException("Panel closed","AbortError"));return;}
        const aborted=()=>{clearTimeout(timer);reject(new DOMException("Panel closed","AbortError"));};
        const timer=setTimeout(()=>{signal?.removeEventListener("abort",aborted);resolve();},until-Date.now());signal?.addEventListener("abort",aborted,{once:true});
      });
      const value=await request("/api/watch",{path},{signal});
      if(owner){owner.webMissingWatches??=new Map();if(value.missing)owner.webMissingWatches.set(path,Date.now()+60000);else owner.webMissingWatches.delete(path);}
      return value;
    })();
  }
  const controller = new AbortController();
  let stopped = false;
  let sleepTimer;
  let wakeSleep;
  const sleep = milliseconds => new Promise(resolve => {
    wakeSleep = resolve;
    sleepTimer = setTimeout(resolve, milliseconds);
  });
  const loop = async () => {
    while (!stopped) {
      try {
        const event = await request("/api/watch", { path }, { signal: controller.signal });
        if (!stopped && event.changed) await changed(event);
        if (event.missing) await sleep(60000);
      } catch (error) {
        if (stopped) break;
        report(error);
        await sleep(60000);
        if (!stopped) await changed({ changed: true, fallback: true });
      }
    }
  };
  const completion = loop();
  return {
    completion,
    dispose() { stopped = true; controller.abort(); clearTimeout(sleepTimer); wakeSleep?.(); },
  };
}

export const model = { snapshot:{workspaces:[],tabs:[],panes:[]}, layout:null, home:"/", selected:selection.read(), selectionRevision:0, programs:{programs:{},shells:[]}, palette:Object.create(null), hooks:{}, shared:stored(localStorage,"herdr-web.shared.v1"), builtins:Object.create(null), personal:stored(localStorage,"herdr-web.personal.v1") };
export const configure = hooks => { model.hooks=hooks; };
const statusOf=value=>({waiting:"blocked",error:"blocked",blocked:"blocked",complete:"done",completed:"done",done:"done",running:"working",working:"working",busy:"working"})[value]||"idle";
export function ingest(value){
  model.snapshot=structuredClone(value.snapshot);model.home=value.home;model.backendLayout=value.layout;
  const available=model.snapshot.workspaces.filter(space=>space.label!=="__herdr_web_parked__");
  const remembered=model.selected.tabsBySpace;
  model.selected.tabsBySpace=Object.fromEntries(available.flatMap(space=>{
    const tab=model.snapshot.tabs.find(tab=>tab.workspace_id===space.workspace_id&&tab.tab_id===remembered?.[space.workspace_id]);
    return tab?[[space.workspace_id,tab.tab_id]]:[];
  }));
  let space=available.find(space=>space.workspace_id===model.selected.space)||available.find(space=>space.workspace_id===model.snapshot.focused_workspace_id)||available[0];
  model.selected.space=space?.workspace_id;
  const tabs=model.snapshot.tabs.filter(tab=>tab.workspace_id===model.selected.space);
  let tab=tabs.find(tab=>tab.tab_id===model.selected.tab)||tabs.find(tab=>tab.tab_id===model.selected.tabsBySpace[space?.workspace_id])||tabs.find(tab=>tab.tab_id===space?.active_tab_id)||tabs[0];
  model.selected.tab=tab?.tab_id;
  if(tab)model.selected.tabsBySpace[tab.workspace_id]=tab.tab_id;
  const panes=model.snapshot.panes.filter(pane=>pane.tab_id===tab?.tab_id);
  model.selected.pane=panes.find(pane=>pane.pane_id===model.selected.pane)?.pane_id||panes[0]?.pane_id;
  if(model.selected.zoom&&!panes.some(pane=>pane.pane_id===model.selected.zoom))model.selected.zoom=null;
  selection.save(model.selected);
}
export const home=()=>model.home;
export const appHome=()=>"/browser";
export const props=()=>currentView()?.webProps;
export const shared=key=>model.shared[key];
export function setShared(key,value){if(JSON.stringify(model.shared[key])===JSON.stringify(value))return;model.shared[key]=value;localStorage.setItem("herdr-web.shared.v1",JSON.stringify(model.shared));model.hooks.render?.();}
export const layout=()=>model.layout;
export const layouts=()=>["main","terminals","win98",...Object.keys(model.personal)];
export function settings(){
  const value=preferences.read(),choice=value.theme&&typeof value.theme==="object"?value.theme:{};
  const legacy=typeof value.theme==="string"?value.theme:null;
  const declared=model.layout?.theme;
  const appearance=["light","dark"].includes(value.appearance)?value.appearance:globalThis.matchMedia?.("(prefers-color-scheme: light)").matches?"light":"dark";
  const theme_dark=choice.dark||value.theme_dark||(legacy&&legacy!=="paper"?legacy:null)||(typeof declared==="string"&&declared!=="paper"?declared:declared?.dark)||"graphite";
  const theme_light=choice.light||value.theme_light||declared?.light||"paper";
  const font=value.font||model.layout?.font||"JetBrains Mono",ui_font=value.ui_font||model.layout?.ui_font||"Geist";
  return {zoom:1,font_size:model.layout?.font_size||14,...value,font,font_family:font,ui_font,ui_font_family:ui_font,appearance,theme_dark,theme_light,
    theme:model.layout?.theme==="win98"?"win98":appearance==="light"?theme_light:theme_dark};
}
export const fonts=()=>["JetBrains Mono","Geist Mono","Geist","Symbols Nerd Font","Pixelated MS Sans Serif","PxPlus IBM VGA 8x16"];
const browserKeys=new Set(["ctrl-t","ctrl-w","ctrl-n","ctrl-1","ctrl-2","ctrl-3","ctrl-4","ctrl-5","ctrl-6","ctrl-7","ctrl-8","ctrl-9","ctrl-tab","ctrl-shift-tab","ctrl-pageup","ctrl-pagedown","ctrl-shift-pageup","ctrl-shift-pagedown","ctrl-shift-n","ctrl-shift-t","ctrl-shift-w","ctrl-shift-b","ctrl-shift-k","ctrl-shift-d","ctrl-shift-e","ctrl-+","ctrl-shift-+","ctrl-=","ctrl--","ctrl-0","alt-left","alt-right"]);
const browserChord=event=>[event.ctrlKey||event.metaKey?"ctrl":null,event.altKey?"alt":null,event.shiftKey?"shift":null,({ArrowLeft:"left",ArrowRight:"right",ArrowUp:"up",ArrowDown:"down",PageUp:"pageup",PageDown:"pagedown"})[event.key]||event.key.toLowerCase()].filter(Boolean).join("-");
export const reservedKey=event=>browserKeys.has(browserChord(event));
export function keys(){const native=model.keymap||{};const result=Object.entries(native).flatMap(([section,values])=>Object.entries(values).filter(([key])=>!browserKeys.has(key)).map(([keys,action])=>({section,keys,action})));return [...result,...Object.entries({"ctrl-shift-p":"app.palette","ctrl-alt-t":"tab.new","ctrl-alt-n":"space.new","ctrl-alt-d":"pane.split:right","ctrl-alt-e":"pane.split:down"}).map(([keys,action])=>({section:"global",keys,action}))];}
export function keyCommand(event,terminal){if(event.getModifierState?.("AltGraph"))return null;const chord=browserChord(event);return keys().find(binding=>binding.keys===chord&&(binding.section==="global"||(terminal&&binding.section==="terminal")))?.action||null;}
export function dispatchKey(action){if(action.startsWith("send:"))return model.hooks.send(action.slice(5));const [name,value]=action.split(":");if(name==="space.select"&&value)return run(name,{id:spaces()[Number(value)-1]?.id});if(name==="tab.select"&&value)return run(name,{id:spaces().find(space=>space.active)?.tabs[Number(value)-1]?.id});return run(name,value?{[name==="mod.open"?"name":name==="layout.toggle"?"id":"direction"]:value}:{});}
export const window=()=>({platform:"linux",active:document.hasFocus(),fullscreen:!!document.fullscreenElement,buttons:true,titlebar:"none",width:innerWidth,height:innerHeight,paint_path:(_path,background)=>div().bg(background)});
export const placement=()=>({width:innerWidth,height:innerHeight,x:0,y:0});
export function programFor(pane){const key=typeof pane.agent==="object"?pane.agent?.name:pane.agent;const source=key&&(model.programs.programs[key]||Object.values(model.programs.programs).find(value=>value.run?.[0]===key));return source?{...source,key,agent:true}:{...model.programs.programs.shell,key:"shell",name:"Terminal",shell:true,agent:false};}
export const panes=()=>model.snapshot.panes.filter(pane=>model.snapshot.workspaces.find(space=>space.workspace_id===pane.workspace_id)?.label!=="__herdr_web_parked__").map(pane=>({pane:pane.pane_id,terminal:pane.terminal_id,tab:pane.tab_id,space:pane.workspace_id,title:pane.label||pane.title||pane.terminal_title||"",cwd:pane.foreground_cwd||pane.cwd,status:statusOf(pane.agent_status),program:programFor(pane),focused:pane.pane_id===model.selected.pane,zoomed:model.selected.zoom===pane.pane_id}));
export const spaces=()=>model.snapshot.workspaces.filter(space=>space.label!=="__herdr_web_parked__").map(space=>({id:space.workspace_id,name:space.label,active:space.workspace_id===model.selected.space,machine:null,folder:model.snapshot.panes.find(pane=>pane.workspace_id===space.workspace_id)?.cwd,status:statusOf(space.agent_status),tabs:model.snapshot.tabs.filter(tab=>tab.workspace_id===space.workspace_id).map(tab=>({id:tab.tab_id,name:tab.label||"Terminal",active:tab.tab_id===model.selected.tab,status:statusOf(tab.agent_status),zoomed:!!model.selected.zoom,settled:!!shared(`tab.settled.${tab.tab_id}`)}))}));
export function terminals(){const records=model.snapshot.panes.map(pane=>({address:pane.terminal_id,id:pane.terminal_id,pane:pane.pane_id,tab:pane.tab_id,space:pane.workspace_id,name:pane.tokens?.web_name?pane.tokens.web_name+(pane.tokens.web_name_tail||""):pane.label||pane.title||programFor(pane).name,cwd:pane.foreground_cwd||pane.cwd,parent:pane.tokens?.web_parent?pane.tokens.web_parent+(pane.tokens.web_parent_tail||""):null,labels:{...pane.tokens,"herdr.right-click":shared(`right_click.${pane.terminal_id}`)||"menu"},status:statusOf(pane.agent_status),program:programFor(pane),alive:true,state:"live"}));const byId=new Map(records.map(value=>[value.address,value]));for(const record of records){const seen=new Set([record.address]);let ancestor=record.parent;for(let depth=0;ancestor;depth++){if(depth>=32||seen.has(ancestor)||!byId.has(ancestor)){record.parent=null;break;}seen.add(ancestor);ancestor=byId.get(ancestor).parent;}}return records;}
export function agents(){const all=terminals(),parked=!!currentView()?.webProps?.parked;const parkedSpaces=new Set(model.snapshot.workspaces.filter(space=>space.label==="__herdr_web_parked__").map(space=>space.workspace_id));return all.filter(term=>parked?parkedSpaces.has(term.space)&&!all.some(parent=>parent.address===term.parent&&parkedSpaces.has(parent.space)):!term.parent&&(term.program.agent||all.some(child=>child.parent===term.address))).map((term,index)=>({...term,terminal:term.address,space_name:model.snapshot.workspaces.find(space=>space.workspace_id===term.space)?.label||"",seq:index,focused:term.pane===model.selected.pane}));}
export const focused=()=>panes().find(pane=>pane.focused)||null;
export const machines=()=>[{name:null,label:"Local server",layout:"ready",connected:true,local:true}];
export const updates=()=>({checking:false,checks:false,app:{ready:false,current:"0.1.0",version:"0.1.0",updates:false},core:{ready:false,current:"0.9.5",version:"0.9.5",managed:"manual installation"},manual:true});
export const telemetry=()=>({enabled:false,supported:false,setting:false,sends:false});
export const setAgentOrder=order=>setShared("agent.order",order);
export const openOverlay=options=>model.hooks.openOverlay(options);
export const closeOverlay=id=>model.hooks.closeOverlay(id);
export const presentUpdateNotes=()=>model.hooks.notice("Updates are manual. Telemetry is disabled.");
const commandWaiters=new Set();
export function nextCommand(owner=currentView()){return new Promise((resolve,reject)=>{const waiter={resolve,reject,owner};commandWaiters.add(waiter);owner?.own(()=>{commandWaiters.delete(waiter);reject(new DOMException("Panel closed","AbortError"));});});}
export const installedPrograms=()=>request("/api/agents");
export async function knownAgents(){const installed=await request("/api/agents");return Object.entries(model.programs.programs).filter(([,value])=>value.run).map(([key,value])=>({key,...value,installed:installed.includes(value.run[0])}));}
export async function hc(args){
  if(args.join(" ")==="machine ls --json")return {code:0,stdout:JSON.stringify({machines:(await request("/api/machines")).map(value=>({...value,name:value.host,target:value.host,connected:true,temporary:false}))}),stderr:""};
  if(args[0]==="machine"&&["add","connect","rm","disconnect"].includes(args[1])&&typeof args[2]==="string"){
    const connect=["add","connect"].includes(args[1]);const opened=connect?globalThis.open("about:blank","_blank"):null;
    try{const value=await request("/api/machines",{action:connect?"connect":"disconnect",host:args[2]});if(opened){opened.opener=null;opened.location=value.url;}else if(connect)model.hooks.notice(`Forward ready at ${value.url}. Allow a new browser window to open it.`);return {code:0,stdout:JSON.stringify(value),stderr:""};}
    catch(error){opened?.close();return {code:1,stdout:JSON.stringify({error:{message:error.message}}),stderr:error.message};}
  }
  throw new Error(`Native desktop command is unavailable: ${args.join(" ")}`);
}
export async function exec(program,args,options={}){if(program==="git"){const response=await fetch("/api/git",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({cwd:options.cwd,args})});const value=await response.json();if(!response.ok&&value.code===undefined)throw new Error(value.error);return value;}if(program==="sh"&&args.length===2&&args[0]==="-c"&&args[1]==='printf %s "$HOME"')return {code:0,stdout:home(),stderr:""};throw new Error(`Unsupported browser process operation: ${program}; use an explicit terminal launch instead.`);}
export const listDirectory=path=>request("/api/files",{action:"list",path});
export async function readTextFile(path){
  if(path==="/browser/settings.json")return JSON.stringify(preferences.read());
  if(path.startsWith("/browser/layouts/")){const name=path.slice(17).replace(/\.json$/,"");return JSON.stringify(model.personal[name]||model.builtins[name]||null);}
  return (await request("/api/files",{action:"read",path})).text;
}
export async function writeTextFile(path,text,previous){if(!path.startsWith("/browser/"))throw new Error("The browser has no generic file-write API");if(previous!==undefined&&(await readTextFile(path))!==previous)throw new Error("Browser preferences changed; retry");const value=JSON.parse(text);if(path==="/browser/settings.json"){preferences.save(value);model.hooks.render?.();return;}if(path.startsWith("/browser/layouts/"))return model.hooks.saveLayout(path.slice(17).replace(/\.json$/,""),value);throw new Error("Unsupported browser preference path");}
export function playSound(source){const path=source==="blocked"?"sounds/request.mp3":source==="done"?"sounds/done.mp3":source;const audio=new Audio(path.startsWith("/")?path:`/desktop/${path}`);return audio.play().catch(error=>model.hooks.notice(`Audio requires a user gesture: ${error.message}`));}
const disabledFeedback=()=>{throw new Error("Feedback submission is disabled in Herdr Web");};
export const feedbackAttachImages=disabledFeedback,feedbackClearImages=disabledFeedback,feedbackPasteImage=disabledFeedback,feedbackPickImages=disabledFeedback,feedbackPreview=disabledFeedback,sendFeedback=disabledFeedback;

const paletteDefaults={graphite:[0x17191d,0xd2d4d9,0x394454,[0x45484f,0xdc8c95,0xa6bd91,0xd8be87,0x96b2d7,0xb5a2ce,0x8cbdc3,0xd2d4d9,0x818793,0xf0a5ad,0xbad5a2,0xead2a1,0xb0cbef,0xcbb7e6,0xa4d5db,0xf0f0f2]],ink:[0x111720,0xcdd6e4,0x344861,[0x414b5a,0xe58c9a,0x89c5aa,0xd9be82,0x91b6ee,0xb9a0e2,0x85c4d0,0xcdd6e4,0x7f91a7,0xf5a5b2,0xa5dec3,0xedd39c,0xaccbfa,0xd0b7f4,0xa0dce5,0xf0f5fc]],moss:[0x151c19,0xcdd7ce,0x354a40,[0x435248,0xdf978d,0xa0c4a2,0xd4bc8b,0x99b9cd,0xbba4c8,0x8bbdb5,0xcdd7ce,0x82968a,0xf1afa3,0xb8dab9,0xe9d2a5,0xb1d0e2,0xd1bddc,0xa4d6ca,0xeff5ee]]};
const hex=value=>`#${Math.round(value).toString(16).padStart(6,"0")}`;
const mix=(left,right,amount)=>hex([16,8,0].reduce((value,shift)=>value|(Math.round(((left>>shift)&255)*(1-amount)+((right>>shift)&255)*amount)<<shift),0));
function colors(name){
  const palette=model.palette[name],light=name==="paper";
  const number=value=>parseInt(value.slice(1),16);
  const [background,foreground,selection,ansi]=palette?.ansi?[number(palette.background),number(palette.foreground),number(palette.selection),palette.ansi.map(number)]:paletteDefaults[name]||paletteDefaults.graphite;
  const ui=light?0x1b1b1e:0xeeeeef;
  const result={background:hex(background),terminal_foreground:hex(foreground),foreground:hex(ui),surface:hex(background),
    canvas:mix(background,light?0xe8e8e2:0,light?.2:.16),chrome:mix(background,light?0xe8e8e2:0,light?.6:.32),
    popover:light?hex(background):mix(background,0xffffff,.06),raised:mix(background,light?0:0xffffff,light?.035:.09),
    hover:light?"#0000000c":"#ffffff0e",selected:light?"#00000012":"#ffffff17",border:light?"#00000017":"#ffffff18",input:light?"#00000026":"#ffffff24",
    muted:mix(background,ui,light?.66:.6),dim:mix(background,ui,light?.46:.38),accent:hex(ansi[4]),accent_foreground:hex(background),
    solid:hex(ui),solid_foreground:light?"#ffffff":mix(background,0,.28),ring:mix(background,ui,.7),selection:hex(selection),cursor:hex(foreground),
    working:light?"#9a6700":"#e5c07b",blocked:light?"#c93c2c":"#f0705f",done:light?"#2f6fe4":"#76a8ff",idle:mix(background,ui,.38),unknown:mix(background,ui,.38)};
  ansi.forEach((value,index)=>result[`ansi${index}`]=hex(value));return {...result,...(palette?.ansi?{}:palette)};
}
export function theme(){const name=settings().theme;return {...colors(name),...(!model.layout?.theme||model.layout.theme===name?model.layout?.colors:{})};}
export const themes=()=>["graphite","ink","moss","paper","atom-one-dark","win98"].map(name=>({name,light:name==="paper",colors:colors(name)}));
const commandNames=["tab.new","tab.rename","tab.close","tab.next","tab.prev","pane.split","pane.close","pane.zoom","pane.go","pane.swap","space.new","space.rename","space.close","space.next","space.prev","layout.switch","layout.reset","layout.clone","layout.edit","terminal.open","terminal.child","terminal.copy","terminal.paste","agent.next","agent.prev","window.minimize","window.maximize","window.close","app.settings","app.help","app.reset","app.zoom_in","app.zoom_out","app.zoom_reset","app.theme"];
export const commands=()=>commandNames.map(name=>({name,args:[],about:name.replace(/[._]/g," ")}));
export function run(name,args={}){return Promise.resolve().then(()=>execute(name,args)).catch(error=>model.hooks.notice(error.message));}
async function execute(name,args){
  const selected=model.selected, targetTerminal=args.terminal||args.address, pane=args.pane||(targetTerminal?model.snapshot.panes.find(pane=>pane.terminal_id===targetTerminal)?.pane_id:selected.pane);
  const rpc=(method,params)=>request("/api/command",{method,params});
  const refresh=()=>model.hooks.refresh();
  const select=async(space,tab,pane)=>{Object.assign(selected,{space,tab,pane,zoom:space===selected.space&&tab===selected.tab?selected.zoom:null});if(space&&tab)selected.tabsBySpace={...selected.tabsBySpace,[space]:tab};model.selectionRevision++;selection.save(selected);await refresh();model.hooks.focus?.(selected.pane);};
  if(name==="space.select"){const space=spaces().find(space=>space.id===args.id);return select(space?.id,space?.tabs.find(tab=>tab.id===selected.tabsBySpace?.[space.id])?.id||space?.tabs[0]?.id);}
  if(name==="tab.select"){const tab=model.snapshot.tabs.find(tab=>tab.tab_id===args.id);return select(tab?.workspace_id,tab?.tab_id);}
  if(name==="pane.focus"||name==="agent.focus"){const target=model.snapshot.panes.find(value=>value.pane_id===pane);if(target){if(model.snapshot.workspaces.find(space=>space.workspace_id===target.workspace_id)?.label==="__herdr_web_parked__")return model.hooks.preview(target.terminal_id);await select(target.workspace_id,target.tab_id,target.pane_id);model.hooks.focus(target.pane_id);}return;}
  if(name==="pane.zoom"){selected.zoom=selected.zoom===pane?null:pane;selection.save(selected);return model.hooks.render();}
  if(name.endsWith(".next")||name.endsWith(".prev")){const list=name.startsWith("space")?spaces():name.startsWith("agent")?agents():spaces().find(space=>space.active)?.tabs||[];if(!list.length)return;const index=list.findIndex(item=>item.active||item.focused), item=list[(index+(name.endsWith("next")?1:list.length-1))%list.length];return run(name.startsWith("space")?"space.select":name.startsWith("agent")?"agent.focus":"tab.select",{id:item.id,terminal:item.address});}
  if(name==="terminal.child"){
    const parent=args.parent||targetTerminal||focused()?.terminal;
    if(!parent)throw new Error("Choose a parent terminal.");
    if(args.argv===undefined)return openOverlay({mod:"new-menu",kind:"popup",width:270,props:{parent}});
    const result=await request("/api/child-terminal",{parent,argv:args.argv,name:args.name||"Worker terminal"});
    await refresh();model.hooks.notice(`Child terminal started: ${args.name||"Worker terminal"}`);
    return model.hooks.preview(result.terminal);
  }
  if(name==="terminal.right_click"){
    const terminal=targetTerminal||focused()?.terminal;
    if(!terminal||!model.snapshot.panes.some(pane=>pane.terminal_id===terminal))throw new Error("Choose an existing terminal.");
    if(!["menu","program"].includes(args.to))throw new Error("Choose menu or program right-click mode.");
    return setShared(`right_click.${terminal}`,args.to);
  }
  if(name==="space.new"||name==="tab.new"||name==="terminal.open"){
    const createSpace=name==="space.new"||!selected.space;
    const params={focus:false,cwd:args.cwd||args.folder||focused()?.cwd||model.home};if(args.name)params.label=args.name;if(!createSpace)params.workspace_id=args.space||selected.space;
    const result=await rpc(createSpace?"workspace.create":"tab.create",params);const root=result.root_pane;
    await select(root.workspace_id,root.tab_id,root.pane_id);if(args.argv?.length)await request("/api/terminal-launch",{pane:root.pane_id,argv:args.argv});return refresh();
  }
  if(["space.rename","tab.rename","pane.rename"].includes(name)){
    const kind=name.split(".")[0], id=args.id||(kind==="space"?selected.space:kind==="tab"?selected.tab:pane);
    if(args.name===undefined)return openOverlay({mod:"prompt",kind:"dialog",width:420,props:{title:`Rename ${kind}`,value:kind==="space"?spaces().find(space=>space.id===id)?.name:kind==="tab"?model.snapshot.tabs.find(tab=>tab.tab_id===id)?.label:panes().find(value=>value.pane===id)?.title,command:{name,args:{id,pane:id}}}});
    await rpc(kind==="space"?"workspace.rename":name,{[kind==="space"?"workspace_id":`${kind}_id`]:id,label:args.name});return refresh();
  }
  if(["space.close","tab.close","pane.close"].includes(name)){const kind=name.split(".")[0];if(!confirm(`Close this ${kind} and its running terminals?`))return;await rpc(kind==="space"?"workspace.close":name,{[kind==="space"?"workspace_id":`${kind}_id`]:args.id||(kind==="space"?selected.space:kind==="tab"?selected.tab:pane)});return refresh();}
  if(name==="space.move"||name==="tab.move"){await rpc(name==="space.move"?"workspace.move":name,{[name.startsWith("space")?"workspace_id":"tab_id"]:args.id,insert_index:Math.max(0,(args.index||1)-1)});return refresh();}
  if(name==="pane.split"){const result=await rpc(name,{target_pane_id:pane,direction:args.direction,focus:false});if(args.focus!==false){selected.zoom=null;await select(result.pane.workspace_id,result.pane.tab_id,result.pane.pane_id);}else await refresh();return result;}
  if(name==="pane.go"){const result=await rpc("pane.neighbor",{pane_id:pane,direction:args.direction});const target=result.neighbor?.pane_id||result.neighbor?.neighbor_pane_id;if(target)return run("pane.focus",{pane:target});return;}
  if(name==="pane.swap"){await rpc(name,{pane_id:pane,direction:args.direction});return refresh();}
  if(["terminal.park","terminal.keep","terminal.kill"].includes(name)){const action=name.split(".")[1];if(action==="kill"&&!confirm("Close this terminal and its running process?"))return;await request("/api/terminal-action",{action,terminal:args.terminal,space:action==="keep"?args.space||selected.space:undefined,confirmed:action==="kill"});return refresh();}
  if(name==="terminal.peek"||name==="terminal.show")return model.hooks.preview(args.terminal||args.address);
  if(name==="terminal.copy")return model.hooks.copy();if(name==="terminal.paste")return model.hooks.paste();
  if(name==="app.palette")return openOverlay({mod:"frame/palette",kind:"dialog",width:620});
  if(name==="app.settings")return openOverlay({mod:"frame/settings",kind:"dialog",width:820,height:640,props:args});
  if(name==="app.help")return openOverlay({mod:"help",kind:"dialog",width:650,height:520});
  if(name==="mod.open")return openOverlay({mod:args.name,kind:"dialog",width:600,height:500,props:args});
  if(name==="app.theme"){
    const chosen=themes().find(theme=>theme.name===args.name);
    if(!chosen)throw new Error("Unknown theme");
    const current=settings(),appearance=chosen.light?"light":"dark";
    preferences.save({...preferences.read(),appearance,theme:{dark:current.theme_dark,light:current.theme_light,[appearance]:args.name}});
    return model.hooks.render();
  }
  if(name.startsWith("app.zoom_")){preferences.save({...preferences.read(),zoom:name.endsWith("reset")?1:Math.min(2,Math.max(.65,settings().zoom+(name.endsWith("in")?.1:-.1)))});return model.hooks.render();}
  if(name==="layout.switch")return model.hooks.switchLayout(args.name||layouts()[(layouts().indexOf(shared("layout")||"main")+1)%layouts().length]);
  if(name==="layout.edit"||name==="layout.clone")return model.hooks.layoutEditor();
  if(name==="layout.reset"||name==="app.reset")return model.hooks.switchLayout("main");
  if(name.startsWith("layout."))return model.hooks.layoutCommand(name,args);
  if(name==="window.maximize")return document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();
  if(name==="window.minimize"||name==="window.close"||name==="app.quit")return model.hooks.notice("Use your browser window controls. Closing this page preserves Herdr terminals.");
  if(name.startsWith("app.update")||name==="app.check_updates"||name==="app.whats_new")return presentUpdateNotes();
  if(name==="tab.settle"||name==="tab.unsettle"){setShared(`tab.settled.${args.id||selected.tab}`,name==="tab.settle");return;}
  throw new Error(`Unsupported browser action: ${name}`);
}
