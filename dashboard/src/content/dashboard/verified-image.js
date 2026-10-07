const SHA256=/^[a-f0-9]{64}$/;
const INLINE_IMAGE=/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\r\n]+$/;
const LOCAL_IMAGE=/^\/__pdd_image\/([a-f0-9]{64})$/;

// Keep the reviewed asset row intact; only its verified delivery address changes.
export function verifiedAssetImage(asset){
 if(asset?.integrity_status!=='verified_local'||typeof asset.sha256!=='string'||asset.sha256.length!==64||!SHA256.test(asset.sha256)||typeof asset.data_url!=='string')return null;
 if(INLINE_IMAGE.test(asset.data_url))return asset.data_url;
 const local=LOCAL_IMAGE.exec(asset.data_url);
 return local&&local[0]===asset.data_url&&local[1]===asset.sha256?asset.data_url:null;
}

export function verifiedCardImage(row,assets){
 if(row?.image_content_status!=='verified_local')return null;
 const asset=assets?.get(row.asset_sha256);
 return asset?.sha256===row.asset_sha256?verifiedAssetImage(asset):null;
}
