import { Element, div, currentView } from "./gpui.js";
export const h_flex=()=>div().flex_row();
export const v_flex=()=>div().flex_col();
export const row=h_flex;
export const column=v_flex;
export const scroll=()=>div().overflow_y_scroll();
let sequence=0;
export class InputState {
  static new(options={}){return new this(options);}
  constructor(options={}){this.options=options;this.text=options.value||"";this.listeners=new Map();this.owner=currentView();this.id=`web-input-${++sequence}`;}
  on(name,callback){this.listeners.set(name,callback);return this;}
  emit(name,event){this.listeners.get(name)?.(event,this.owner?.cx);}
  value(){return this.text;}
  set_value(value){this.text=String(value);if(this.node)this.node.value=this.text;}
  set_selected_range(range){this.range=range;this.node?.setSelectionRange(range.start,range.end);}
  focus(){this.wantsFocus=true;this.node?.focus();}
  release(){this.listeners.clear();this.node=null;}
}
export class TextareaState extends InputState {}
export class Input {
  static new(state){return new Element(state instanceof TextareaState?"textarea":"input").id(state.id).attr("type","text").attr("placeholder",state.options.placeholder||"")
    .style("minWidth","0").style("outline","none").style("background","transparent").style("border","0").style("font","inherit").style("color","inherit")
    .on("webmount",node=>{state.node=node;if(document.activeElement!==node&&node.value!==state.text)node.value=state.text;if(state.wantsFocus){state.wantsFocus=false;queueMicrotask(()=>{if(node.isConnected){node.focus();if(state.range)node.setSelectionRange(state.range.start,state.range.end);}});}})
    .on("input",event=>{state.text=event.currentTarget.value;state.emit("change",event);})
    .on("keydown",event=>{if(event.key==="enter"&&!(state instanceof TextareaState)){event.preventDefault();state.emit("submit",event);}})
    .on("focus",event=>state.emit("focus",event)).on("blur",event=>state.emit("blur",event));}
}
export class Textarea extends Input {}
export class EditorState extends InputState {}
export class Editor {
  static new(state){
    if(!state.options.readonly)return Input.new(state);
    return new Element("pre").attr("class","web-preview").owned_content().on("webmount",node=>{
      if(node.webText===state.text)return;node.webText=state.text;node.replaceChildren();
      if(state.text.length>200000||!globalThis.hljs?.getLanguage(state.options.language)){node.append(document.createTextNode(state.text));return;}
      const highlighted=globalThis.hljs.highlight(state.text,{language:state.options.language,ignoreIllegals:true}).value;
      const parsed=new DOMParser().parseFromString(highlighted,"text/html");
      const append=(parent,item)=>{if(item.nodeType===3)parent.append(document.createTextNode(item.textContent));else{const span=document.createElement("span");if(/^hljs-[\w -]+$/.test(item.className||""))span.className=item.className;for(const child of item.childNodes)append(span,child);parent.append(span);}};
      for(const item of parsed.body.childNodes)append(node,item);
    });
  }
}
export const Scrollbar={vertical:id=>{const bar=div().attr("aria-hidden","true").style("pointerEvents","none");bar.mode=()=>bar;bar.viewport_from_layout=()=>bar;bar.on("webmount",()=>{const target=document.getElementById(id);if(target)target.style.overflowY="auto";});return bar;}};
export function v_virtual_list(id,count,height,key,render){return v_flex().id(id).overflow_y_scroll().children(render({start:0,end:Math.min(count,1500)}).map((item,index)=>item.id(`${id}-${key(index)}`).min_h(height)));}
export const input=(value,onChange)=>new Element("input").attr("value",value).on("input",event=>onChange(event.currentTarget.value));
export const textPreview=text=>new Element("pre").attr("class","web-preview").child(String(text));
export const diffRows=rows=>v_flex().children(rows.slice(0,1500).map((value,index)=>div().id(`diff-${index}`).child(String(value))));
