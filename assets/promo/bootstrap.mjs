// Keep the native film available even if WebGL or a rendering dependency fails.
// Dynamic import also catches errors raised before the renderer can initialize.
function showNativeFilm(error){
  const $=id=>document.getElementById(id);
  $('film').hidden=true;
  $('loading').hidden=true;
  $('start').hidden=true;
  document.querySelector('.transport').hidden=true;
  document.querySelector('.chapters').hidden=true;
  $('record').hidden=true;
  const video=$('finished-film');
  video.querySelector('source').src=video.querySelector('source').dataset.src;
  video.hidden=false;
  video.preload='metadata';
  video.addEventListener('error',()=>{
    $('status').textContent='MP4 成片暂时无法载入。请刷新页面，或下载 MP4 后观看。';
  });
  video.load();
  $('status').textContent='已切换到 MP4 成片。点击画面中的播放按钮，观看完整旁白、配乐和字幕。';
  console.warn('3D preview unavailable; using the finished MP4.',error);
}

async function startPromo(){
  try{
    const film=await import('./film.mjs');
    await film.filmReady;
  }catch(error){
    showNativeFilm(error);
  }
}
void startPromo();
