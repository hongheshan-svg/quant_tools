// 检查所有嵌套 Mach-O，修复 PyInstaller 复制后失效的签名，再交给 builder 签名。
const path = require('node:path')
const { execFileSync } = require('node:child_process')

exports.default = async function afterPackMacos(context) {
  if (context.electronPlatformName !== 'darwin') return
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  execFileSync('bash', [path.join(__dirname, '../../../scripts/macos_signature_audit.sh'), app], { stdio: 'inherit' })
}
