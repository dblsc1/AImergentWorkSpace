// 服务端宠物素材下载（desktop.pet.v1「三·2」）：compose 一次性服务 pets-download 跑它，下到卷 honeycomb_pets_data。
// 已下好的文件按大小跳过；失败的下次 up 再续。永远 exit 0（不让 up --wait 因它失败）。
const { downloadAll, writeIndex } = require('./assets-download');

async function run({ destDir, baseUrl, stillsBaseUrl, manifest = require('./manifest.json'), stills = require('./manifest-stills.json'), moodMap = require('./mood-map.json'), fetch: fetchFn = fetch, log = console.log }) {
  const onProgress = (id, err) => err && log(`失败 ${id}: ${err.message}`);
  const failed = (await downloadAll({ manifest, moodMap, destDir, fetch: fetchFn, ...(baseUrl && { baseUrl }), onProgress })).failed
    .concat((await downloadAll({ manifest: stills, destDir, fetch: fetchFn, ...(stillsBaseUrl && { baseUrl: stillsBaseUrl }), onProgress })).failed);   // 静态形象（「三·5」）
  writeIndex(destDir);
  log(failed.length ? `宠物素材：${failed.length} 个文件没下完，下次 up 续` : '宠物素材：已齐');
  return failed;
}

if (require.main === module) {
  run({ destDir: process.env.PETS_DEST || '/pets', baseUrl: process.env.PETS_BASE_URL, stillsBaseUrl: process.env.PETS_STILLS_BASE_URL })
    .catch(e => console.log(`宠物素材下载出错：${e.message}`));
}

module.exports = { run };
