use crate::error::{Error, Result};
use serde_json::{json, Value};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD, engine::general_purpose::STANDARD};
use std::{sync::Arc, time::Duration};
use tokio::{io::{AsyncReadExt, AsyncWriteExt}, net::UnixStream, sync::{mpsc, watch}};
use axum::extract::ws::{WebSocket, Message};

pub const FRAME_LIMIT: usize = 32 * 1024 * 1024;
pub const PIXEL_LIMIT: u64 = 8_388_608;
pub const SCENE_LIMIT: usize = 16 * 1024 * 1024;

pub struct Cursor<'input> { input: &'input [u8], offset: usize }

impl<'input> Cursor<'input> {
    pub fn new(input: &'input [u8]) -> Result<Self> {
        if input.len() > FRAME_LIMIT { return Err(Error::bad("native frame exceeds 32 MiB")); }
        Ok(Self { input, offset: 0 })
    }

    pub fn take(&mut self, length: usize) -> Result<&'input [u8]> {
        let end = self.offset.checked_add(length).filter(|end| *end <= self.input.len())
            .ok_or_else(|| Error::bad("truncated binary field"))?;
        let bytes = &self.input[self.offset..end];
        self.offset = end;
        Ok(bytes)
    }

    pub fn integer(&mut self) -> Result<u64> {
        match self.take(1)?[0] {
            value @ 0..=250 => Ok(u64::from(value)),
            251 => Ok(u64::from(u16::from_le_bytes(self.take(2)?.try_into().unwrap()))),
            252 => Ok(u64::from(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))),
            253 => Ok(u64::from_le_bytes(self.take(8)?.try_into().unwrap())),
            _ => Err(Error::bad("unsupported binary integer tag")),
        }
    }

    pub fn bounded_bytes(&mut self, limit: usize) -> Result<&'input [u8]> {
        let length = usize::try_from(self.integer()?).map_err(|_| Error::bad("binary length overflows host size"))?;
        if length > limit { return Err(Error::bad("binary field exceeds limit")); }
        self.take(length)
    }

    pub fn finish(&self) -> Result<()> {
        if self.offset != self.input.len() { return Err(Error::bad("unexpected trailing binary fields")); }
        Ok(())
    }
    fn count(&mut self, limit: usize) -> Result<usize> { let value=self.integer()?; if value>limit as u64{return Err(Error::bad("native count exceeds limit"));}Ok(value as usize) }
    fn boolean(&mut self) -> Result<bool> { match self.take(1)?[0] {0=>Ok(false),1=>Ok(true),_=>Err(Error::bad("invalid native Boolean"))} }
    fn string(&mut self) -> Result<String> { String::from_utf8(self.bounded_bytes(65536)?.to_vec()).map_err(|_|Error::bad("native string is not UTF-8")) }
    fn rect(&mut self) -> Result<[u64;4]> { Ok([self.integer()?,self.integer()?,self.integer()?,self.integer()?]) }
    fn frame(&mut self) -> Result<()> {
        let cells=self.count(1_000_000)?;
        for _ in 0..cells { self.bounded_bytes(4096)?;self.integer()?;self.integer()?;self.integer()?;self.boolean()?;if self.boolean()?{self.integer()?;} }
        let width=self.count(u16::MAX as usize)?;let height=self.count(u16::MAX as usize)?;
        if width.checked_mul(height)!=Some(cells){return Err(Error::bad("native frame dimensions do not match its cells"));}
        if self.boolean()?{self.integer()?;self.integer()?;self.boolean()?;self.take(1)?;}
        for _ in 0..self.count(100000)?{self.bounded_bytes(65536)?;}
        self.bounded_bytes(FRAME_LIMIT)?;Ok(())
    }
    fn asset_key(&mut self) -> Result<AssetKey> {
        let start=self.offset;
        let pane=match self.integer()?{
            0=>{let target=self.integer()?;if target>1{return Err(Error::bad("unknown native graphics target"));}let id=self.string()?;self.integer()?;if target==0{Some(id)}else{None}},
            1=>{let pane=self.string()?;self.string()?;Some(pane)},
            _=>return Err(Error::bad("unknown native graphics source")),
        };
        let width=u32::try_from(self.integer()?).map_err(|_|Error::bad("image width overflow"))?;
        let height=u32::try_from(self.integer()?).map_err(|_|Error::bad("image height overflow"))?;
        let format=self.count(2)? as u8;
        let length=self.integer()?;self.integer()?;
        Ok(AssetKey{key:URL_SAFE_NO_PAD.encode(&self.input[start..self.offset]),pane,width,height,format,length})
    }
}

struct AssetKey { key:String, pane:Option<String>, width:u32, height:u32, format:u8, length:u64 }
struct Scene { boot:String, rect:Option<[u64;4]>, value:Value }

fn text_patch(cursor:&mut Cursor<'_>)->Result<String>{
    let boot=cursor.string()?;
    for _ in 0..3{cursor.integer()?;}
    let mut cells=0usize;
    for _ in 0..cursor.count(100000)?{
        cursor.count(u16::MAX as usize)?;cursor.count(u16::MAX as usize)?;
        let count=cursor.count(1_000_000)?;
        cells=cells.checked_add(count).filter(|count|*count<=1_000_000).ok_or_else(||Error::bad("native patch exceeds cell limit"))?;
        for _ in 0..count{cursor.bounded_bytes(4096)?;cursor.integer()?;cursor.integer()?;cursor.integer()?;cursor.boolean()?;if cursor.boolean()?{cursor.integer()?;}}
    }
    for _ in 0..cursor.count(4096)?{
        cursor.string()?;cursor.integer()?;cursor.rect()?;cursor.rect()?;
        if cursor.boolean()?{cursor.rect()?;}
        if cursor.boolean()?{cursor.integer()?;cursor.integer()?;cursor.integer()?;}
        for _ in 0..4{cursor.boolean()?;}
        cursor.integer()?;cursor.integer()?;
    }
    if cursor.boolean()?{cursor.integer()?;cursor.integer()?;cursor.boolean()?;cursor.take(1)?;}
    cursor.finish()?;Ok(boot)
}

fn surface(cursor:&mut Cursor<'_>, target:&str)->Result<Scene>{
    let boot=cursor.string()?;cursor.integer()?;cursor.integer()?;cursor.frame()?;
    let mut inner=None;
    for _ in 0..cursor.count(4096)?{
        let pane=cursor.string()?;cursor.integer()?;cursor.rect()?;let rect=cursor.rect()?;
        if pane==target{inner=Some(rect);}
        if cursor.boolean()?{cursor.rect()?;}
        if cursor.boolean()?{cursor.integer()?;cursor.integer()?;cursor.integer()?;}
        for _ in 0..4{cursor.boolean()?;}
        cursor.integer()?;cursor.integer()?;
    }
    for _ in 0..cursor.count(4096)?{cursor.count(1)?;cursor.integer()?;cursor.rect()?;cursor.rect()?;for _ in 0..cursor.count(32)?{cursor.boolean()?;}}
    if cursor.boolean()?{
        cursor.string()?;cursor.string()?;
        for _ in 0..2{if cursor.boolean()?{match cursor.integer()?{0=>{cursor.integer()?;},1=>{cursor.take(1)?;},_=>return Err(Error::bad("unknown popup size"))}}}
        cursor.frame()?;cursor.boolean()?;cursor.boolean()?;cursor.integer()?;cursor.integer()?;
    }
    let mut assets=Vec::new();let mut placements=Vec::new();let mut retained=std::collections::BTreeSet::new();let mut total=0;
    for _ in 0..cursor.count(1024)?{
        let key=cursor.asset_key()?;let bytes=cursor.bounded_bytes(SCENE_LIMIT)?;
        total+=bytes.len();if total>SCENE_LIMIT{return Err(Error::bad("native image scene exceeds 16 MiB"));}
        if bytes.len() as u64!=key.length{return Err(Error::bad("native image key length mismatch"));}
        validate_image(key.width,key.height,key.format,bytes)?;
        if key.pane.as_deref()==Some(target){retained.insert(key.key.clone());assets.push(json!({"key":key.key,"width":key.width,"height":key.height,"format":key.format,"data":STANDARD.encode(bytes)}));}
    }
    for _ in 0..cursor.count(10000)?{
        let key=cursor.asset_key()?;
        let id=cursor.integer()?;let left=cursor.integer()?;let top=cursor.integer()?;
        let cols=cursor.integer()?;let rows=cursor.integer()?;let source_x=cursor.integer()?;let source_y=cursor.integer()?;let source_width=cursor.integer()?;let source_height=cursor.integer()?;let offset_x=cursor.integer()?;let offset_y=cursor.integer()?;let signed=cursor.integer()?;let z=((signed>>1) as i64)^-((signed&1) as i64);cursor.integer()?;
        if key.pane.as_deref()==Some(target){if let Some(rect)=inner{retained.insert(key.key.clone());placements.push(json!({"key":key.key,"id":id,"x":left as i64-rect[0] as i64,"y":top as i64-rect[1] as i64,"cols":cols,"rows":rows,"sx":source_x,"sy":source_y,"sw":source_width,"sh":source_height,"ox":offset_x,"oy":offset_y,"z":z}));}}
    }
    for _ in 0..cursor.count(4096)?{let key=cursor.asset_key()?;if key.pane.as_deref()==Some(target){retained.insert(key.key);}}
    Ok(Scene{boot,rect:inner,value:json!({"assets":assets,"placements":placements,"retained":retained})})
}

fn put_integer(bytes:&mut Vec<u8>,value:u64){if value<=250{bytes.push(value as u8);}else if value<=u16::MAX as u64{bytes.push(251);bytes.extend_from_slice(&(value as u16).to_le_bytes());}else if value<=u32::MAX as u64{bytes.push(252);bytes.extend_from_slice(&(value as u32).to_le_bytes());}else{bytes.push(253);bytes.extend_from_slice(&value.to_le_bytes());}}
fn put_string(bytes:&mut Vec<u8>,value:&str){put_integer(bytes,value.len() as u64);bytes.extend_from_slice(value.as_bytes());}
async fn send(writer:&mut tokio::net::unix::OwnedWriteHalf,bytes:Vec<u8>)->Result<()>{tokio::time::timeout(Duration::from_secs(5),async{writer.write_all(&(bytes.len() as u32).to_le_bytes()).await?;writer.write_all(&bytes).await}).await.map_err(|_|Error::backend("native writer stalled"))??;Ok(())}
fn viewport(value:&Value)->Result<[u64;4]>{use crate::validation::integer;let cols=integer(&value["cols"],"cols",1,500)?;let rows=integer(&value["rows"],"rows",1,200)?;let cell=|name:&str,max:f64|value[name].as_f64().filter(|value|value.is_finite()&&*value>=1.0&&*value<=max).map(|value|value.round() as u64).ok_or_else(||Error::bad(format!("invalid {name}")));Ok([cols,rows,cell("cw",100.0)?,cell("ch",200.0)?])}
async fn resize(writer:&mut tokio::net::unix::OwnedWriteHalf,size:[u64;4])->Result<()>{let mut bytes=vec![12];for value in [size[2],size[3],size[0],size[1]]{put_integer(&mut bytes,value);}bytes.push(0);send(writer,bytes).await}

pub async fn serve(mut browser:WebSocket,backend:Arc<crate::backend::Backend>,pane:String,mut shutdown:watch::Receiver<bool>){
    let result:Result<()>=async{
        let target=backend.pane(&pane).await?;let tab=target["tab_id"].as_str().ok_or_else(||Error::backend("pane has no tab"))?.to_owned();
        let initial=tokio::time::timeout(Duration::from_secs(10),browser.recv()).await.map_err(|_|Error::bad("graphics viewport required within ten seconds"))?;
        let Some(Ok(Message::Text(text)))=initial else{return Err(Error::bad("expected initial graphics viewport"));};
        let mut wanted=viewport(&serde_json::from_str(&text).map_err(|_|Error::bad("invalid graphics viewport"))?)?;
        let stem=backend.socket.file_stem().and_then(|value|value.to_str()).ok_or_else(||Error::bad("invalid selected socket filename"))?;
        let native_path=backend.socket.with_file_name(format!("{stem}-client.sock"));
        let stream=UnixStream::connect(native_path).await?;let (mut reader,mut writer)=stream.into_split();
        let hello=json!({"generation":1,"cell_width_px":wanted[2],"cell_height_px":wanted[3],"surface_size":{"cols":wanted[0]+1,"rows":wanted[1]},"pixel_mouse":false,"direct_graphics":false,"endpoint_keybindings":false,"mouse_capture":false,"surface_active":true,"surface_reuse":false,"surface_delta":false,"surface_scroll":false,"snapshot_codecs":["shell.snapshot.v1"],"surface_codecs":["shell.surface.v1"],"input_codecs":["shell.input.semantic.v1"],"blob_codecs":["shell.blob.v1"]});
        let mut bytes=vec![20];put_string(&mut bytes,"endpoint.hello.v1");put_string(&mut bytes,&hello.to_string());send(&mut writer,bytes).await?;
        let (frames,mut received)=mpsc::channel::<Result<Vec<u8>>>(2);
        let reader_task=tokio::spawn(async move{loop{let Ok(slot)=frames.reserve().await else{break;};let result=async{let length=reader.read_u32_le().await? as usize;if length==0||length>FRAME_LIMIT{return Err(Error::bad("native frame exceeds 32 MiB"));}let mut frame=vec![0;length];reader.read_exact(&mut frame).await?;Ok(frame)}.await;let failed=result.is_err();slot.send(result);if failed{break;}}});
        struct ReaderOwner(tokio::task::JoinHandle<()>);impl Drop for ReaderOwner{fn drop(&mut self){self.0.abort();}}
        let _reader=ReaderOwner(reader_task);
        let mut welcomed=false;let mut boot=String::new();let mut selected=false;let mut corrections=0;let mut size=[wanted[0]+1,wanted[1],wanted[2],wanted[3]];
        let welcome_deadline=tokio::time::Instant::now()+Duration::from_secs(10);
        loop{
            tokio::select!{
                _=async{let _=shutdown.wait_for(|value|*value).await;}=>break,
                _=tokio::time::sleep_until(welcome_deadline),if !welcomed=>return Err(Error::backend("native endpoint welcome timed out")),
                message=browser.recv()=>match message{
                    Some(Ok(Message::Text(text)))=>{wanted=viewport(&serde_json::from_str(&text).map_err(|_|Error::bad("invalid viewport"))?)?;size=[wanted[0]+1,wanted[1],wanted[2],wanted[3]];corrections=0;resize(&mut writer,size).await?;},
                    Some(Ok(Message::Ping(_)))|Some(Ok(Message::Pong(_)))=>(),_=>break,
                },
                frame=received.recv()=>{
                    let frame=frame.ok_or_else(||Error::backend("native graphics connection ended"))??;let mut cursor=Cursor::new(&frame)?;
                    match cursor.integer()?{
                        20=>{let kind=cursor.string()?;let data:Value=serde_json::from_str(&cursor.string()?).map_err(|_|Error::bad("invalid endpoint JSON"))?;
                            if matches!(kind.as_str(),"endpoint.surface-delta.v1"|"endpoint.surface-scroll.v1"|"endpoint.surface-reuse.v1"){return Err(Error::backend("native endpoint used a disabled surface encoding"));}
                            if kind=="endpoint.welcome.v1"{if data["generation"]!=1||data.get("error").is_some_and(|value|!value.is_null()){return Err(Error::backend("incompatible native endpoint generation"));}for (field,codec) in [("snapshot_codec","shell.snapshot.v1"),("surface_codec","shell.surface.v1"),("input_codec","shell.input.semantic.v1"),("blob_codec","shell.blob.v1")]{if data[field]!=codec{return Err(Error::backend(format!("unsupported native codec {field}")));}}welcomed=true;}
                            if kind=="shell.snapshot.v1"{let current=data["boot_id"].as_str().ok_or_else(||Error::bad("missing endpoint boot ID"))?;if !boot.is_empty()&&boot!=current{return Err(Error::backend("Herdr boot changed; reconnect graphics"));}boot=current.to_owned();}
                        },
                        13=>{if !welcomed{return Err(Error::backend("native surface before compatible welcome"));}let scene=surface(&mut cursor,&pane)?;cursor.finish()?;if !boot.is_empty()&&boot!=scene.boot{return Err(Error::backend("Herdr boot changed"));}boot=scene.boot;
                            if let Some(rect)=scene.rect{if selected&&corrections<4&&rect[2]>0&&rect[3]>0&&(rect[2]!=wanted[0]||rect[3]!=wanted[1]){size[0]=(size[0]*wanted[0]/rect[2]+1).clamp(2,5000);size[1]=(size[1]*wanted[1]/rect[3]).clamp(1,2000);if size[0]*size[1]>1_000_000{return Err(Error::bad("native viewport correction exceeds cell bound"));}corrections+=1;resize(&mut writer,size).await?;}
                                let text=scene.value.to_string();if text.len()>24*1024*1024{return Err(Error::bad("encoded graphics scene exceeds 24 MiB"));}tokio::time::timeout(Duration::from_secs(5),browser.send(Message::Text(text.into()))).await.map_err(|_|Error::backend("graphics browser stalled"))?.map_err(|error|Error::backend(error.to_string()))?;
                            }
                        },
                        18=>{cursor.string()?;cursor.string()?;cursor.boolean()?;let chunk=cursor.bounded_bytes(1024*1024)?;if let Ok(value)=serde_json::from_slice::<Value>(chunk){if value.get("error").is_some(){return Err(Error::backend(value["error"]["message"].as_str().unwrap_or("native command failed")));}}},
                        0|12 if !welcomed=>return Err(Error::backend("incompatible native endpoint: expected generation-1 welcome")),
                        15=>return Err(Error::backend(cursor.string()?)),
                        3=>return Err(Error::backend("Herdr native endpoint is shutting down")),
                        19=>{if !welcomed{return Err(Error::backend("native text patch before compatible welcome"));}let current=text_patch(&mut cursor)?;if boot.is_empty()||boot!=current{return Err(Error::backend("native text patch has an incompatible boot ID"));}},
                        tag if tag<=20=>(),_=>return Err(Error::backend("unknown native envelope variant")),
                    }
                    if welcomed&&!selected&&!boot.is_empty(){let request=json!({"id":"web-graphics-tab","method":"tab.focus","params":{"tab_id":tab}}).to_string();let mut bytes=vec![15];put_string(&mut bytes,&boot);put_string(&mut bytes,&request);send(&mut writer,bytes).await?;selected=true;}
                }
            }
        }Ok(())
    }.await;
    if let Err(error)=result{let _=tokio::time::timeout(Duration::from_secs(1),browser.send(Message::Text(json!({"error":error.1}).to_string().into()))).await;}
}

pub fn validate_image(width: u32, height: u32, format: u8, bytes: &[u8]) -> Result<()> {
    let pixels = u64::from(width) * u64::from(height);
    if width == 0 || height == 0 || pixels > PIXEL_LIMIT || bytes.len() > SCENE_LIMIT {
        return Err(Error::bad("image exceeds dimension or byte limits"));
    }
    match format {
        0 | 1 => {
            let channels = if format == 0 { 3 } else { 4 };
            if pixels * channels != bytes.len() as u64 { return Err(Error::bad("raw image channel length mismatch")); }
        }
        2 => {
            if bytes.len() < 33 || !bytes.starts_with(b"\x89PNG\r\n\x1a\n") || &bytes[8..16] != b"\0\0\0\rIHDR" {
                return Err(Error::bad("invalid PNG header"));
            }
            let png_width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
            let png_height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
            if (png_width, png_height) != (width, height) { return Err(Error::bad("PNG dimensions do not match image metadata")); }
        }
        _ => return Err(Error::bad("unsupported decoded image format")),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integers_and_truncations_are_bounded() {
        for (encoded, expected) in [
            (vec![250], 250), (vec![251, 251, 0], 251),
            (vec![252, 0, 0, 1, 0], 65536),
            (vec![253, 0, 0, 0, 0, 1, 0, 0, 0], 4294967296),
        ] {
            assert_eq!(Cursor::new(&encoded).unwrap().integer().unwrap(), expected);
            for length in 0..encoded.len() {
                assert!(Cursor::new(&encoded[..length]).unwrap().integer().is_err());
            }
        }
        assert!(Cursor::new(&[254]).unwrap().integer().is_err());
        assert!(Cursor::new(&[255]).unwrap().integer().is_err());
        assert!(Cursor::new(&[251, 0, 1]).unwrap().bounded_bytes(10).is_err());
    }

    #[test]
    fn raw_images_require_exact_lengths() {
        assert!(validate_image(2, 2, 0, &[0; 12]).is_ok());
        assert!(validate_image(2, 2, 1, &[0; 16]).is_ok());
        assert!(validate_image(2, 2, 1, &[0; 15]).is_err());
        assert!(validate_image(u32::MAX, u32::MAX, 0, &[]).is_err());
        assert!(validate_image(0, 1, 1, &[]).is_err());
        assert!(validate_image(1, 1, 3, &[0; 4]).is_err());
    }

    fn sample_surface(fingerprint:u64)->Vec<u8>{
        let mut bytes=Vec::new();
        put_string(&mut bytes,"boot-fixture");put_integer(&mut bytes,7);put_integer(&mut bytes,9);
        bytes.extend_from_slice(&[0;6]);
        put_integer(&mut bytes,1);put_string(&mut bytes,"pane:猫");put_integer(&mut bytes,12);
        for value in [0,0,12,8,1,1,10,6]{put_integer(&mut bytes,value);}
        bytes.extend_from_slice(&[0,0,1,0,0,0]);put_integer(&mut bytes,80);put_integer(&mut bytes,96);
        bytes.extend_from_slice(&[0,0]);
        let mut key=vec![0,0];put_string(&mut key,"pane:猫");
        for value in [42,2,2,1,16,fingerprint]{put_integer(&mut key,value);}
        put_integer(&mut bytes,1);bytes.extend_from_slice(&key);put_integer(&mut bytes,16);bytes.extend_from_slice(&[255,0,0,255,0,255,0,255,0,0,255,255,255,255,255,255]);
        put_integer(&mut bytes,1);bytes.extend_from_slice(&key);
        for value in [17,2,3,1,1,0,0,2,2,3,4,1,0]{put_integer(&mut bytes,value);}
        put_integer(&mut bytes,1);bytes.extend_from_slice(&key);
        bytes
    }

    #[test]
    fn full_scenes_preserve_keys_crop_offsets_and_signed_stacking(){
        let bytes=sample_surface(123456789);
        let mut cursor=Cursor::new(&bytes).unwrap();
        let scene=surface(&mut cursor,"pane:猫").unwrap();cursor.finish().unwrap();
        assert_eq!(scene.boot,"boot-fixture");assert_eq!(scene.rect,Some([1,1,10,6]));
        let placement=&scene.value["placements"][0];
        assert_eq!(placement["x"],1);assert_eq!(placement["y"],2);assert_eq!(placement["ox"],3);assert_eq!(placement["oy"],4);assert_eq!(placement["z"],-1);
        assert_eq!(placement["sw"],2);assert_eq!(scene.value["assets"][0]["format"],1);
        assert_eq!(placement["key"],scene.value["retained"][0]);
        let changed=sample_surface(123456790);let mut other=Cursor::new(&changed).unwrap();
        assert_ne!(placement["key"],surface(&mut other,"pane:猫").unwrap().value["placements"][0]["key"]);
        let mut unrelated=Cursor::new(&bytes).unwrap();let unrelated=surface(&mut unrelated,"different-pane").unwrap();
        assert_eq!(unrelated.value["assets"],json!([]));assert_eq!(unrelated.value["placements"],json!([]));
    }

    #[test]
    fn every_truncated_full_scene_is_rejected_without_unbounded_allocation(){
        let bytes=sample_surface(123456789);
        for length in 0..bytes.len(){let mut cursor=Cursor::new(&bytes[..length]).unwrap();assert!(surface(&mut cursor,"pane:猫").is_err(),"accepted prefix {length}");}
        let mut oversized=Vec::new();put_integer(&mut oversized,1_000_001);
        assert!(Cursor::new(&oversized).unwrap().frame().is_err());
        assert!(Cursor::new(&[2]).unwrap().boolean().is_err());
    }

    #[test]
    fn legacy_cell_patches_are_validated_without_changing_image_scenes(){
        let mut bytes=Vec::new();put_string(&mut bytes,"boot-fixture");
        for value in [1,10,11,1,0,0,1]{put_integer(&mut bytes,value);}
        put_string(&mut bytes,"猫");
        for value in [0,0,0,0,0,0,0]{put_integer(&mut bytes,value);}
        assert_eq!(text_patch(&mut Cursor::new(&bytes).unwrap()).unwrap(),"boot-fixture");
        for length in 0..bytes.len(){assert!(text_patch(&mut Cursor::new(&bytes[..length]).unwrap()).is_err(),"accepted patch prefix {length}");}
        let mut oversized=Vec::new();put_string(&mut oversized,"boot-fixture");
        for value in [1,10,11,1,0,0,1_000_001]{put_integer(&mut oversized,value);}
        assert!(text_patch(&mut Cursor::new(&oversized).unwrap()).is_err());
    }
}
