/* @name 上架自检
 * @tag 测试
 * @desc 用来验证自动上架流程的临时插件，验证完会删掉 */
plugin({ name: '上架自检', tag: '测试', stats() { return [['状态', '正常']]; } });
