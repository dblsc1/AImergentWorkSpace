// 服务端宠物素材下载（desktop.pet.v1「三·2」）：compose 一次性服务 pets-download 跑它，下到卷 honeycomb_pets_data。
// 已下好的文件按大小跳过；失败的下次 up 再续。永远 exit 0（不让 up --wait 因它失败）。
const { downloadAll } = require('./assets-download');

async function run({ destDir, baseUrl, manifest = require('./manifest.json'), moodMap = require('./mood-map.json'), log = console.log }) {
  const { failed } = await downloadAll({
    manifest, moodMap, destDir, ...(baseUrl && { baseUrl }),
    onProgress: (id, err) => err && log(`失败 ${id}: ${err.message}`),
  });
  log(failed.length ? `宠物素材：${failed.length} 个文件没下完，下次 up 续` : '宠物素材：已齐');
  return failed;
}

if (require.main === module) {
  run({ destDir: process.env.PETS_DEST || '/pets', baseUrl: process.env.PETS_BASE_URL })
    .catch(e => console.log(`宠物素材下载出错：${e.message}`));
}

module.exports = { run };
