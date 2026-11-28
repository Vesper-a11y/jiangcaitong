/* 酱菜通 · 江财（金智 KINGOSOFT）APK 数据层
   ------------------------------------------------------------------
   与 web/jxufe_eams.py（已端到端实测通过）一一对应。native.js 里的
   上游学校正方逻辑保留作对照；本文件独立暴露 window.JxufeApi，
   native.js 的 handle() 在江财模式下优先调用这里。

   网络通道：复用 Android.req2(id, method, path, body, referer, headersJson)
   返回体由 Java 侧按 Content-Type charset 解码（见 NativeBridge.readAll），
   所以这里拿到的已经是正确编码的字符串。

   Cookie：登录态在 Java 侧全局 CookieManager 里。粘贴的 JSESSIONID
   通过 Android.eamsCookieSet() 注入；取数前先探 showYearTerm.action 是否有效。
   ------------------------------------------------------------------ */
(function () {
  'use strict';
  /* ---- INLINE CRYPTO START ------------------------------------------------
     登录加密：逐字取自 jxufe_scripts/jkingo.des.js 的 strEnc 及加密路径的全部
     依赖函数（解密路径 strDec/dec/hexToBt4/hexToBt64/byteToString 用不到，未内联），
     以及 jxufe_scripts/md5.js 解包后的原文 hex_md5。APK 打包清单可能漏新文件，故内联。
     金智 DES 不是标准 DES（自定义 initPermute 表），不能用 Web Crypto 替代。
     两组实现各自包在独立 IIFE 里，只向外暴露 strEnc / hex_md5，不污染全局。
     ---------------------------------------------------------------------- */
  var strEnc = (function () {
    // jkingo.des.js 里 i/j/k/m/n 是隐式全局循环变量；本文件 use strict，必须显式声明。
    var i, j, k, m, n;
function strEnc(data,firstKey,secondKey,thirdKey){

 var leng = data.length;
 var encData = "";
 var firstKeyBt,secondKeyBt,thirdKeyBt,firstLength,secondLength,thirdLength;
 if(firstKey != null && firstKey != ""){    
   firstKeyBt = getKeyBytes(firstKey);
   firstLength = firstKeyBt.length;
 }
 if(secondKey != null && secondKey != ""){
   secondKeyBt = getKeyBytes(secondKey);
   secondLength = secondKeyBt.length;
 }
 if(thirdKey != null && thirdKey != ""){
   thirdKeyBt = getKeyBytes(thirdKey);
   thirdLength = thirdKeyBt.length;
 }  
 
 if(leng > 0){
   if(leng < 4){
     var bt = strToBt(data);      
     var encByte ;
     if(firstKey != null && firstKey !="" && secondKey != null && secondKey != "" && thirdKey != null && thirdKey != ""){
       var tempBt;
       var x,y,z;
       tempBt = bt;        
       for(x = 0;x < firstLength ;x ++){
         tempBt = enc(tempBt,firstKeyBt[x]);
       }
       for(y = 0;y < secondLength ;y ++){
         tempBt = enc(tempBt,secondKeyBt[y]);
       }
       for(z = 0;z < thirdLength ;z ++){
         tempBt = enc(tempBt,thirdKeyBt[z]);
       }        
       encByte = tempBt;        
     }else{
       if(firstKey != null && firstKey !="" && secondKey != null && secondKey != ""){
         var tempBt;
         var x,y;
         tempBt = bt;
         for(x = 0;x < firstLength ;x ++){
           tempBt = enc(tempBt,firstKeyBt[x]);
         }
         for(y = 0;y < secondLength ;y ++){
           tempBt = enc(tempBt,secondKeyBt[y]);
         }
         encByte = tempBt;
       }else{
         if(firstKey != null && firstKey !=""){            
           var tempBt;
           var x = 0;
           tempBt = bt;            
           for(x = 0;x < firstLength ;x ++){
             tempBt = enc(tempBt,firstKeyBt[x]);
           }
           encByte = tempBt;
         }
       }        
     }
     encData = bt64ToHex(encByte);
   }else{
     var iterator = parseInt(leng/4);
     var remainder = leng%4;
     var i=0;      
     for(i = 0;i < iterator;i++){
       var tempData = data.substring(i*4+0,i*4+4);
       var tempByte = strToBt(tempData);
       var encByte ;
       if(firstKey != null && firstKey !="" && secondKey != null && secondKey != "" && thirdKey != null && thirdKey != ""){
         var tempBt;
         var x,y,z;
         tempBt = tempByte;
         for(x = 0;x < firstLength ;x ++){
           tempBt = enc(tempBt,firstKeyBt[x]);
         }
         for(y = 0;y < secondLength ;y ++){
           tempBt = enc(tempBt,secondKeyBt[y]);
         }
         for(z = 0;z < thirdLength ;z ++){
           tempBt = enc(tempBt,thirdKeyBt[z]);
         }
         encByte = tempBt;
       }else{
         if(firstKey != null && firstKey !="" && secondKey != null && secondKey != ""){
           var tempBt;
           var x,y;
           tempBt = tempByte;
           for(x = 0;x < firstLength ;x ++){
             tempBt = enc(tempBt,firstKeyBt[x]);
           }
           for(y = 0;y < secondLength ;y ++){
             tempBt = enc(tempBt,secondKeyBt[y]);
           }
           encByte = tempBt;
         }else{
           if(firstKey != null && firstKey !=""){                      
             var tempBt;
             var x;
             tempBt = tempByte;
             for(x = 0;x < firstLength ;x ++){                
               tempBt = enc(tempBt,firstKeyBt[x]);
             }
             encByte = tempBt;              
           }
         }
       }
       encData += bt64ToHex(encByte);
     }      
     if(remainder > 0){
       var remainderData = data.substring(iterator*4+0,leng);
       var tempByte = strToBt(remainderData);
       var encByte ;
       if(firstKey != null && firstKey !="" && secondKey != null && secondKey != "" && thirdKey != null && thirdKey != ""){
         var tempBt;
         var x,y,z;
         tempBt = tempByte;
         for(x = 0;x < firstLength ;x ++){
           tempBt = enc(tempBt,firstKeyBt[x]);
         }
         for(y = 0;y < secondLength ;y ++){
           tempBt = enc(tempBt,secondKeyBt[y]);
         }
         for(z = 0;z < thirdLength ;z ++){
           tempBt = enc(tempBt,thirdKeyBt[z]);
         }
         encByte = tempBt;
       }else{
         if(firstKey != null && firstKey !="" && secondKey != null && secondKey != ""){
           var tempBt;
           var x,y;
           tempBt = tempByte;
           for(x = 0;x < firstLength ;x ++){
             tempBt = enc(tempBt,firstKeyBt[x]);
           }
           for(y = 0;y < secondLength ;y ++){
             tempBt = enc(tempBt,secondKeyBt[y]);
           }
           encByte = tempBt;
         }else{
           if(firstKey != null && firstKey !=""){            
             var tempBt;
             var x;
             tempBt = tempByte;
             for(x = 0;x < firstLength ;x ++){
               tempBt = enc(tempBt,firstKeyBt[x]);
             }
             encByte = tempBt;
           }
         }
       }
       encData += bt64ToHex(encByte);
     }                
   }
 }
 return encData;
}

function getKeyBytes(key){
 var keyBytes = new Array();
 var leng = key.length;
 var iterator = parseInt(leng/4);
 var remainder = leng%4;
 var i = 0;
 for(i = 0;i < iterator; i ++){
   keyBytes[i] = strToBt(key.substring(i*4+0,i*4+4));
 }
 if(remainder > 0){
   keyBytes[i] = strToBt(key.substring(i*4+0,leng));
 }    
 return keyBytes;
}

function strToBt(str){  
 var leng = str.length;
 var bt = new Array(64);
 if(leng < 4){
   var i=0,j=0,p=0,q=0;
   for(i = 0;i<leng;i++){
     var k = str.charCodeAt(i);
     for(j=0;j<16;j++){      
       var pow=1,m=0;
       for(m=15;m>j;m--){
         pow *= 2;
       }        
       bt[16*i+j]=parseInt(k/pow)%2;
     }
   }
   for(p = leng;p<4;p++){
     var k = 0;
     for(q=0;q<16;q++){      
       var pow=1,m=0;
       for(m=15;m>q;m--){
         pow *= 2;
       }        
       bt[16*p+q]=parseInt(k/pow)%2;
     }
   }  
 }else{
   for(i = 0;i<4;i++){
     var k = str.charCodeAt(i);
     for(j=0;j<16;j++){      
       var pow=1;
       for(m=15;m>j;m--){
         pow *= 2;
       }        
       bt[16*i+j]=parseInt(k/pow)%2;
     }
   }  
 }
 return bt;
}

function bt4ToHex(binary) {
 var hex;
 switch (binary) {
   case "0000" : hex = "0"; break;
   case "0001" : hex = "1"; break;
   case "0010" : hex = "2"; break;
   case "0011" : hex = "3"; break;
   case "0100" : hex = "4"; break;
   case "0101" : hex = "5"; break;
   case "0110" : hex = "6"; break;
   case "0111" : hex = "7"; break;
   case "1000" : hex = "8"; break;
   case "1001" : hex = "9"; break;
   case "1010" : hex = "A"; break;
   case "1011" : hex = "B"; break;
   case "1100" : hex = "C"; break;
   case "1101" : hex = "D"; break;
   case "1110" : hex = "E"; break;
   case "1111" : hex = "F"; break;
 }
 return hex;
}

function bt64ToHex(byteData){
 var hex = "";
 for(i = 0;i<16;i++){
   var bt = "";
   for(j=0;j<4;j++){    
     bt += byteData[i*4+j];
   }    
   hex+=bt4ToHex(bt);
 }
 return hex;
}

function enc(dataByte,keyByte){  
 var keys = generateKeys(keyByte);    
 var ipByte   = initPermute(dataByte);  
 var ipLeft   = new Array(32);
 var ipRight  = new Array(32);
 var tempLeft = new Array(32);
 var i = 0,j = 0,k = 0,m = 0, n = 0;
 for(k = 0;k < 32;k ++){
   ipLeft[k] = ipByte[k];
   ipRight[k] = ipByte[32+k];
 }    
 for(i = 0;i < 16;i ++){
   for(j = 0;j < 32;j ++){
     tempLeft[j] = ipLeft[j];
     ipLeft[j] = ipRight[j];      
   }  
   var key = new Array(48);
   for(m = 0;m < 48;m ++){
     key[m] = keys[i][m];
   }
   var  tempRight = xor(pPermute(sBoxPermute(xor(expandPermute(ipRight),key))), tempLeft);      
   for(n = 0;n < 32;n ++){
     ipRight[n] = tempRight[n];
   }  
   
 }  
 
 
 var finalData =new Array(64);
 for(i = 0;i < 32;i ++){
   finalData[i] = ipRight[i];
   finalData[32+i] = ipLeft[i];
 }
 return finallyPermute(finalData);  
}

function initPermute(originalData){
 var ipByte = new Array(64);
 for (i = 0, m = 1, n = 0; i < 4; i++, m += 2, n += 2) {
   for (j = 7, k = 0; j >= 0; j--, k++) {
     ipByte[i * 8 + k] = originalData[j * 8 + m];
     ipByte[i * 8 + k + 32] = originalData[j * 8 + n];
   }
 }    
 return ipByte;
}

function expandPermute(rightData){  
 var epByte = new Array(48);
 for (i = 0; i < 8; i++) {
   if (i == 0) {
     epByte[i * 6 + 0] = rightData[31];
   } else {
     epByte[i * 6 + 0] = rightData[i * 4 - 1];
   }
   epByte[i * 6 + 1] = rightData[i * 4 + 0];
   epByte[i * 6 + 2] = rightData[i * 4 + 1];
   epByte[i * 6 + 3] = rightData[i * 4 + 2];
   epByte[i * 6 + 4] = rightData[i * 4 + 3];
   if (i == 7) {
     epByte[i * 6 + 5] = rightData[0];
   } else {
     epByte[i * 6 + 5] = rightData[i * 4 + 4];
   }
 }      
 return epByte;
}

function xor(byteOne,byteTwo){  
 var xorByte = new Array(byteOne.length);
 for(i = 0;i < byteOne.length; i ++){      
   xorByte[i] = byteOne[i] ^ byteTwo[i];
 }  
 return xorByte;
}

function sBoxPermute(expandByte){
 
   var sBoxByte = new Array(32);
   var binary = "";
   var s1 = [
       [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7],
       [0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8],
       [4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0],
       [15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13 ]];

       /* Table - s2 */
   var s2 = [
       [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10],
       [3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5],
       [0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15],
       [13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9 ]];

       /* Table - s3 */
   var s3= [
       [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8],
       [13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1],
       [13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7],
       [1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12 ]];
       /* Table - s4 */
   var s4 = [
       [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15],
       [13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9],
       [10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4],
       [3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14 ]];

       /* Table - s5 */
   var s5 = [
       [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9],
       [14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6],
       [4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14],
       [11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3 ]];

       /* Table - s6 */
   var s6 = [
       [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11],
       [10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8],
       [9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6],
       [4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13 ]];

       /* Table - s7 */
   var s7 = [
       [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1],
       [13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6],
       [1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2],
       [6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12]];

       /* Table - s8 */
   var s8 = [
       [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7],
       [1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2],
       [7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8],
       [2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11]];
   
   for(m=0;m<8;m++){
   var i=0,j=0;
   i = expandByte[m*6+0]*2+expandByte[m*6+5];
   j = expandByte[m * 6 + 1] * 2 * 2 * 2 
     + expandByte[m * 6 + 2] * 2* 2 
     + expandByte[m * 6 + 3] * 2 
     + expandByte[m * 6 + 4];
   switch (m) {
     case 0 :
       binary = getBoxBinary(s1[i][j]);
       break;
     case 1 :
       binary = getBoxBinary(s2[i][j]);
       break;
     case 2 :
       binary = getBoxBinary(s3[i][j]);
       break;
     case 3 :
       binary = getBoxBinary(s4[i][j]);
       break;
     case 4 :
       binary = getBoxBinary(s5[i][j]);
       break;
     case 5 :
       binary = getBoxBinary(s6[i][j]);
       break;
     case 6 :
       binary = getBoxBinary(s7[i][j]);
       break;
     case 7 :
       binary = getBoxBinary(s8[i][j]);
       break;
   }      
   sBoxByte[m*4+0] = parseInt(binary.substring(0,1));
   sBoxByte[m*4+1] = parseInt(binary.substring(1,2));
   sBoxByte[m*4+2] = parseInt(binary.substring(2,3));
   sBoxByte[m*4+3] = parseInt(binary.substring(3,4));
 }
 return sBoxByte;
}

function pPermute(sBoxByte){
 var pBoxPermute = new Array(32);
 pBoxPermute[ 0] = sBoxByte[15]; 
 pBoxPermute[ 1] = sBoxByte[ 6]; 
 pBoxPermute[ 2] = sBoxByte[19]; 
 pBoxPermute[ 3] = sBoxByte[20]; 
 pBoxPermute[ 4] = sBoxByte[28]; 
 pBoxPermute[ 5] = sBoxByte[11]; 
 pBoxPermute[ 6] = sBoxByte[27]; 
 pBoxPermute[ 7] = sBoxByte[16]; 
 pBoxPermute[ 8] = sBoxByte[ 0]; 
 pBoxPermute[ 9] = sBoxByte[14]; 
 pBoxPermute[10] = sBoxByte[22]; 
 pBoxPermute[11] = sBoxByte[25]; 
 pBoxPermute[12] = sBoxByte[ 4]; 
 pBoxPermute[13] = sBoxByte[17]; 
 pBoxPermute[14] = sBoxByte[30]; 
 pBoxPermute[15] = sBoxByte[ 9]; 
 pBoxPermute[16] = sBoxByte[ 1]; 
 pBoxPermute[17] = sBoxByte[ 7]; 
 pBoxPermute[18] = sBoxByte[23]; 
 pBoxPermute[19] = sBoxByte[13]; 
 pBoxPermute[20] = sBoxByte[31]; 
 pBoxPermute[21] = sBoxByte[26]; 
 pBoxPermute[22] = sBoxByte[ 2]; 
 pBoxPermute[23] = sBoxByte[ 8]; 
 pBoxPermute[24] = sBoxByte[18]; 
 pBoxPermute[25] = sBoxByte[12]; 
 pBoxPermute[26] = sBoxByte[29]; 
 pBoxPermute[27] = sBoxByte[ 5]; 
 pBoxPermute[28] = sBoxByte[21]; 
 pBoxPermute[29] = sBoxByte[10]; 
 pBoxPermute[30] = sBoxByte[ 3]; 
 pBoxPermute[31] = sBoxByte[24];    
 return pBoxPermute;
}

function finallyPermute(endByte){    
 var fpByte = new Array(64);  
 fpByte[ 0] = endByte[39]; 
 fpByte[ 1] = endByte[ 7]; 
 fpByte[ 2] = endByte[47]; 
 fpByte[ 3] = endByte[15]; 
 fpByte[ 4] = endByte[55]; 
 fpByte[ 5] = endByte[23]; 
 fpByte[ 6] = endByte[63]; 
 fpByte[ 7] = endByte[31]; 
 fpByte[ 8] = endByte[38]; 
 fpByte[ 9] = endByte[ 6]; 
 fpByte[10] = endByte[46]; 
 fpByte[11] = endByte[14]; 
 fpByte[12] = endByte[54]; 
 fpByte[13] = endByte[22]; 
 fpByte[14] = endByte[62]; 
 fpByte[15] = endByte[30]; 
 fpByte[16] = endByte[37]; 
 fpByte[17] = endByte[ 5]; 
 fpByte[18] = endByte[45]; 
 fpByte[19] = endByte[13]; 
 fpByte[20] = endByte[53]; 
 fpByte[21] = endByte[21]; 
 fpByte[22] = endByte[61]; 
 fpByte[23] = endByte[29]; 
 fpByte[24] = endByte[36]; 
 fpByte[25] = endByte[ 4]; 
 fpByte[26] = endByte[44]; 
 fpByte[27] = endByte[12]; 
 fpByte[28] = endByte[52]; 
 fpByte[29] = endByte[20]; 
 fpByte[30] = endByte[60]; 
 fpByte[31] = endByte[28]; 
 fpByte[32] = endByte[35]; 
 fpByte[33] = endByte[ 3]; 
 fpByte[34] = endByte[43]; 
 fpByte[35] = endByte[11]; 
 fpByte[36] = endByte[51]; 
 fpByte[37] = endByte[19]; 
 fpByte[38] = endByte[59]; 
 fpByte[39] = endByte[27]; 
 fpByte[40] = endByte[34]; 
 fpByte[41] = endByte[ 2]; 
 fpByte[42] = endByte[42]; 
 fpByte[43] = endByte[10]; 
 fpByte[44] = endByte[50]; 
 fpByte[45] = endByte[18]; 
 fpByte[46] = endByte[58]; 
 fpByte[47] = endByte[26]; 
 fpByte[48] = endByte[33]; 
 fpByte[49] = endByte[ 1]; 
 fpByte[50] = endByte[41]; 
 fpByte[51] = endByte[ 9]; 
 fpByte[52] = endByte[49]; 
 fpByte[53] = endByte[17]; 
 fpByte[54] = endByte[57]; 
 fpByte[55] = endByte[25]; 
 fpByte[56] = endByte[32]; 
 fpByte[57] = endByte[ 0]; 
 fpByte[58] = endByte[40]; 
 fpByte[59] = endByte[ 8]; 
 fpByte[60] = endByte[48]; 
 fpByte[61] = endByte[16]; 
 fpByte[62] = endByte[56]; 
 fpByte[63] = endByte[24];
 return fpByte;
}

function getBoxBinary(i) {
 var binary = "";
 switch (i) {
   case 0 :binary = "0000";break;
   case 1 :binary = "0001";break;
   case 2 :binary = "0010";break;
   case 3 :binary = "0011";break;
   case 4 :binary = "0100";break;
   case 5 :binary = "0101";break;
   case 6 :binary = "0110";break;
   case 7 :binary = "0111";break;
   case 8 :binary = "1000";break;
   case 9 :binary = "1001";break;
   case 10 :binary = "1010";break;
   case 11 :binary = "1011";break;
   case 12 :binary = "1100";break;
   case 13 :binary = "1101";break;
   case 14 :binary = "1110";break;
   case 15 :binary = "1111";break;
 }
 return binary;
}

function generateKeys(keyByte){    
 var key   = new Array(56);
 var keys = new Array();  
 
 keys[ 0] = new Array();
 keys[ 1] = new Array();
 keys[ 2] = new Array();
 keys[ 3] = new Array();
 keys[ 4] = new Array();
 keys[ 5] = new Array();
 keys[ 6] = new Array();
 keys[ 7] = new Array();
 keys[ 8] = new Array();
 keys[ 9] = new Array();
 keys[10] = new Array();
 keys[11] = new Array();
 keys[12] = new Array();
 keys[13] = new Array();
 keys[14] = new Array();
 keys[15] = new Array();  
 var loop = [1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1];

 for(i=0;i<7;i++){
   for(j=0,k=7;j<8;j++,k--){
     key[i*8+j]=keyByte[8*k+i];
   }
 }    
 
 var i = 0;
 for(i = 0;i < 16;i ++){
   var tempLeft=0;
   var tempRight=0;
   for(j = 0; j < loop[i];j ++){          
     tempLeft = key[0];
     tempRight = key[28];
     for(k = 0;k < 27 ;k ++){
       key[k] = key[k+1];
       key[28+k] = key[29+k];
     }  
     key[27]=tempLeft;
     key[55]=tempRight;
   }
   var tempKey = new Array(48);
   tempKey[ 0] = key[13];
   tempKey[ 1] = key[16];
   tempKey[ 2] = key[10];
   tempKey[ 3] = key[23];
   tempKey[ 4] = key[ 0];
   tempKey[ 5] = key[ 4];
   tempKey[ 6] = key[ 2];
   tempKey[ 7] = key[27];
   tempKey[ 8] = key[14];
   tempKey[ 9] = key[ 5];
   tempKey[10] = key[20];
   tempKey[11] = key[ 9];
   tempKey[12] = key[22];
   tempKey[13] = key[18];
   tempKey[14] = key[11];
   tempKey[15] = key[ 3];
   tempKey[16] = key[25];
   tempKey[17] = key[ 7];
   tempKey[18] = key[15];
   tempKey[19] = key[ 6];
   tempKey[20] = key[26];
   tempKey[21] = key[19];
   tempKey[22] = key[12];
   tempKey[23] = key[ 1];
   tempKey[24] = key[40];
   tempKey[25] = key[51];
   tempKey[26] = key[30];
   tempKey[27] = key[36];
   tempKey[28] = key[46];
   tempKey[29] = key[54];
   tempKey[30] = key[29];
   tempKey[31] = key[39];
   tempKey[32] = key[50];
   tempKey[33] = key[44];
   tempKey[34] = key[32];
   tempKey[35] = key[47];
   tempKey[36] = key[43];
   tempKey[37] = key[48];
   tempKey[38] = key[38];
   tempKey[39] = key[55];
   tempKey[40] = key[33];
   tempKey[41] = key[52];
   tempKey[42] = key[45];
   tempKey[43] = key[41];
   tempKey[44] = key[49];
   tempKey[45] = key[35];
   tempKey[46] = key[28];
   tempKey[47] = key[31];
   switch(i){
     case 0: for(m=0;m < 48 ;m++){ keys[ 0][m] = tempKey[m]; } break;
     case 1: for(m=0;m < 48 ;m++){ keys[ 1][m] = tempKey[m]; } break;
     case 2: for(m=0;m < 48 ;m++){ keys[ 2][m] = tempKey[m]; } break;
     case 3: for(m=0;m < 48 ;m++){ keys[ 3][m] = tempKey[m]; } break;
     case 4: for(m=0;m < 48 ;m++){ keys[ 4][m] = tempKey[m]; } break;
     case 5: for(m=0;m < 48 ;m++){ keys[ 5][m] = tempKey[m]; } break;
     case 6: for(m=0;m < 48 ;m++){ keys[ 6][m] = tempKey[m]; } break;
     case 7: for(m=0;m < 48 ;m++){ keys[ 7][m] = tempKey[m]; } break;
     case 8: for(m=0;m < 48 ;m++){ keys[ 8][m] = tempKey[m]; } break;
     case 9: for(m=0;m < 48 ;m++){ keys[ 9][m] = tempKey[m]; } break;
     case 10: for(m=0;m < 48 ;m++){ keys[10][m] = tempKey[m]; } break;
     case 11: for(m=0;m < 48 ;m++){ keys[11][m] = tempKey[m]; } break;
     case 12: for(m=0;m < 48 ;m++){ keys[12][m] = tempKey[m]; } break;
     case 13: for(m=0;m < 48 ;m++){ keys[13][m] = tempKey[m]; } break;
     case 14: for(m=0;m < 48 ;m++){ keys[14][m] = tempKey[m]; } break;
     case 15: for(m=0;m < 48 ;m++){ keys[15][m] = tempKey[m]; } break;
   }
 }
 return keys;  
}
    return strEnc;
  })();

  var hex_md5 = (function () {
var hexcase=0;var b64pad="";var chrsz=8;function hex_md5(s){return binl2hex(core_md5(str2binl(s),s.length*chrsz))}
function b64_md5(s){return binl2b64(core_md5(str2binl(s),s.length*chrsz))}
function str_md5(s){return binl2str(core_md5(str2binl(s),s.length*chrsz))}
function hex_hmac_md5(key,data){return binl2hex(core_hmac_md5(key,data))}
function b64_hmac_md5(key,data){return binl2b64(core_hmac_md5(key,data))}
function str_hmac_md5(key,data){return binl2str(core_hmac_md5(key,data))}
function md5_vm_test(){return hex_md5("abc")=="900150983cd24fb0d6963f7d28e17f72"}
function core_md5(x,len){x[len>>5]|=0x80<<((len)%32);
x[(((len+64)>>>9)<<4)+14]=len;var a=1732584193;var b=-271733879;var c=-1732584194;var d=271733878;for(var i=0;i<x.length;i+=16){var olda=a;var oldb=b;var oldc=c;var oldd=d;a=md5_ff(a,b,c,d,x[i+0],7,-680876936);
d=md5_ff(d,a,b,c,x[i+1],12,-389564586);
c=md5_ff(c,d,a,b,x[i+2],17,606105819);
b=md5_ff(b,c,d,a,x[i+3],22,-1044525330);
a=md5_ff(a,b,c,d,x[i+4],7,-176418897);
d=md5_ff(d,a,b,c,x[i+5],12,1200080426);
c=md5_ff(c,d,a,b,x[i+6],17,-1473231341);
b=md5_ff(b,c,d,a,x[i+7],22,-45705983);
a=md5_ff(a,b,c,d,x[i+8],7,1770035416);
d=md5_ff(d,a,b,c,x[i+9],12,-1958414417);
c=md5_ff(c,d,a,b,x[i+10],17,-42063);
b=md5_ff(b,c,d,a,x[i+11],22,-1990404162);
a=md5_ff(a,b,c,d,x[i+12],7,1804603682);
d=md5_ff(d,a,b,c,x[i+13],12,-40341101);
c=md5_ff(c,d,a,b,x[i+14],17,-1502002290);
b=md5_ff(b,c,d,a,x[i+15],22,1236535329);
a=md5_gg(a,b,c,d,x[i+1],5,-165796510);
d=md5_gg(d,a,b,c,x[i+6],9,-1069501632);
c=md5_gg(c,d,a,b,x[i+11],14,643717713);
b=md5_gg(b,c,d,a,x[i+0],20,-373897302);
a=md5_gg(a,b,c,d,x[i+5],5,-701558691);
d=md5_gg(d,a,b,c,x[i+10],9,38016083);
c=md5_gg(c,d,a,b,x[i+15],14,-660478335);
b=md5_gg(b,c,d,a,x[i+4],20,-405537848);
a=md5_gg(a,b,c,d,x[i+9],5,568446438);
d=md5_gg(d,a,b,c,x[i+14],9,-1019803690);
c=md5_gg(c,d,a,b,x[i+3],14,-187363961);
b=md5_gg(b,c,d,a,x[i+8],20,1163531501);
a=md5_gg(a,b,c,d,x[i+13],5,-1444681467);
d=md5_gg(d,a,b,c,x[i+2],9,-51403784);
c=md5_gg(c,d,a,b,x[i+7],14,1735328473);
b=md5_gg(b,c,d,a,x[i+12],20,-1926607734);
a=md5_hh(a,b,c,d,x[i+5],4,-378558);
d=md5_hh(d,a,b,c,x[i+8],11,-2022574463);
c=md5_hh(c,d,a,b,x[i+11],16,1839030562);
b=md5_hh(b,c,d,a,x[i+14],23,-35309556);
a=md5_hh(a,b,c,d,x[i+1],4,-1530992060);
d=md5_hh(d,a,b,c,x[i+4],11,1272893353);
c=md5_hh(c,d,a,b,x[i+7],16,-155497632);
b=md5_hh(b,c,d,a,x[i+10],23,-1094730640);
a=md5_hh(a,b,c,d,x[i+13],4,681279174);
d=md5_hh(d,a,b,c,x[i+0],11,-358537222);
c=md5_hh(c,d,a,b,x[i+3],16,-722521979);
b=md5_hh(b,c,d,a,x[i+6],23,76029189);
a=md5_hh(a,b,c,d,x[i+9],4,-640364487);
d=md5_hh(d,a,b,c,x[i+12],11,-421815835);
c=md5_hh(c,d,a,b,x[i+15],16,530742520);
b=md5_hh(b,c,d,a,x[i+2],23,-995338651);
a=md5_ii(a,b,c,d,x[i+0],6,-198630844);
d=md5_ii(d,a,b,c,x[i+7],10,1126891415);
c=md5_ii(c,d,a,b,x[i+14],15,-1416354905);
b=md5_ii(b,c,d,a,x[i+5],21,-57434055);
a=md5_ii(a,b,c,d,x[i+12],6,1700485571);
d=md5_ii(d,a,b,c,x[i+3],10,-1894986606);
c=md5_ii(c,d,a,b,x[i+10],15,-1051523);
b=md5_ii(b,c,d,a,x[i+1],21,-2054922799);
a=md5_ii(a,b,c,d,x[i+8],6,1873313359);
d=md5_ii(d,a,b,c,x[i+15],10,-30611744);
c=md5_ii(c,d,a,b,x[i+6],15,-1560198380);
b=md5_ii(b,c,d,a,x[i+13],21,1309151649);
a=md5_ii(a,b,c,d,x[i+4],6,-145523070);
d=md5_ii(d,a,b,c,x[i+11],10,-1120210379);
c=md5_ii(c,d,a,b,x[i+2],15,718787259);
b=md5_ii(b,c,d,a,x[i+9],21,-343485551);
a=safe_add(a,olda);
b=safe_add(b,oldb);
c=safe_add(c,oldc);
d=safe_add(d,oldd)}return Array(a,b,c,d)}
function md5_cmn(q,a,b,x,s,t){return safe_add(bit_rol(safe_add(safe_add(a,q),safe_add(x,t)),s),b)}
function md5_ff(a,b,c,d,x,s,t){return md5_cmn((b&c)|((~b)&d),a,b,x,s,t)}
function md5_gg(a,b,c,d,x,s,t){return md5_cmn((b&d)|(c&(~d)),a,b,x,s,t)}
function md5_hh(a,b,c,d,x,s,t){return md5_cmn(b^c^d,a,b,x,s,t)}
function md5_ii(a,b,c,d,x,s,t){return md5_cmn(c^(b|(~d)),a,b,x,s,t)}
function core_hmac_md5(key,data){var bkey=str2binl(key);
if(bkey.length>16)bkey=core_md5(bkey,key.length*chrsz);
var ipad=Array(16),opad=Array(16);
for(var i=0;i<16;i++){ipad[i]=bkey[i]^0x36363636;opad[i]=bkey[i]^0x5C5C5C5C}var hash=core_md5(ipad.concat(str2binl(data)),512+data.length*chrsz);
return core_md5(opad.concat(hash),512+128)}
function safe_add(x,y){var lsw=(x&0xFFFF)+(y&0xFFFF);
var msw=(x>>16)+(y>>16)+(lsw>>16);
return(msw<<16)|(lsw&0xFFFF)}
function bit_rol(num,cnt){return(num<<cnt)|(num>>>(32-cnt))}
function str2binl(str){var bin=Array();
var mask=(1<<chrsz)-1;for(var i=0;i<str.length*chrsz;i+=chrsz)bin[i>>5]|=(str.charCodeAt(i/chrsz)&mask)<<(i%32);
return bin}
function binl2str(bin){var str="";var mask=(1<<chrsz)-1;for(var i=0;i<bin.length*32;i+=chrsz)str+=String.fromCharCode((bin[i>>5]>>>(i%32))&mask);
return str}
function binl2hex(binarray){var hex_tab=hexcase?"0123456789ABCDEF":"0123456789abcdef";var str="";for(var i=0;i<binarray.length*4;i++){str+=hex_tab.charAt((binarray[i>>2]>>((i%4)*8+4))&0xF)+hex_tab.charAt((binarray[i>>2]>>((i%4)*8))&0xF)}return str}
function binl2b64(binarray){var tab="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";var str="";for(var i=0;i<binarray.length*4;i+=3){var triplet=(((binarray[i>>2]>>8*(i%4))&0xFF)<<16)|(((binarray[i+1>>2]>>8*((i+1)%4))&0xFF)<<8)|((binarray[i+2>>2]>>8*((i+2)%4))&0xFF);
for(var j=0;j<4;j++){if(i*8+j*6>binarray.length*32)str+=b64pad;else str+=tab.charAt((triplet>>6*(3-j))&0x3F)}}return str}
    return hex_md5;
  })();

  /** md5.js 的 str2binl 按 Latin-1 取字节；先转成 UTF-8 字节串，
      才能和 Python hashlib.md5(s.encode("utf-8")).hexdigest() 对齐。 */
  function md5HexUtf8(s) {
    var bytes = new TextEncoder().encode(s);
    var bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return hex_md5(bin);
  }
  /* ---- INLINE CRYPTO END -------------------------------------------------- */

  if (!window.Android || typeof window.Android.req !== 'function') return;
  var Android = window.Android;   // 裸引用在 WebView 里等于 window.Android；这里显式绑定便于测试

  var BASE = 'https://jwxt.jxufe.edu.cn';
  var HOME = BASE + '/frame/homes.action';
  var SCHOOL_CODE = '10421';
  var UA = 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 ' +
           '(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36';

  var POLITE_MS = 1200;
  var lastReq = 0;
  var seq = 100000;                 // 和 native.js 的 seq 避开，防止回调串号
  var waiting = {};

  // 原生回调入口（native.js 已注册 __nativeDone；这里挂自己的分发）
  window.__jxufeDone = function (id, json) {
    var w = waiting[id];
    if (!w) return;
    delete waiting[id];
    try { w.resolve(JSON.parse(json)); } catch (e) { w.reject(new Error('解析响应失败')); }
  };

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /** 发请求。返回 {status, body, url}。headers 可覆盖默认 iframe 头。 */
  /* ---- 网页登录态的「原样转发」（2026-11-23）--------------------------------
   * 用户反馈：网页里自检明确是**通**的（拿到了 userCode），但一搬进 App 就
   * 「登录态已过期」。说明差的不只是那一串 cookie —— 学校那边还认别的东西。
   * 两个已知的嫌疑，一起按住：
   *   ① **Cookie 头**：`getCookie(url)` 只返回 path 匹配该地址的那几条，
   *      会话若挂在 `/jw/` 之类子路径上，抄过来的就不是它。这里把网页里读到的
   *      **整行原样**当作 `Cookie` 请求头发出去，绕开「cookie 罐子里到底存成
   *      什么样」这一层。
   *   ② **User-Agent**：登录窗口走的是电脑版 UA（见 WebActivity.applyMode），
   *      会话是在那个 UA 下建的。取数却用手机 UA —— 学校一旦按 UA 绑会话，
   *      就成了「网页通、App 过期」。这里也一起对上。
   * 只在教务域生效；发给统一身份认证（ssl）的请求不掺这些。 */
  var webLoginCookie = '';
  var webLoginUA = '';

  function overrideHeaders(path, headers) {
    var h = headers || {};
    if (!webLoginCookie && !webLoginUA) return h;
    // 绝对地址发给 CAS 的，不能带教务的 cookie —— service 参数里也含有
    // `jwxt.jxufe.edu.cn` 这几个字，所以只能按**前缀**判，不能 indexOf。
    if (/^https?:\/\//.test(path) && path.indexOf('https://jwxt.jxufe.edu.cn') !== 0
            && path.indexOf('http://jwxt.jxufe.edu.cn') !== 0) {
      return h;
    }
    var out = {};
    Object.keys(h).forEach(function (k) { out[k] = h[k]; });
    if (webLoginCookie) out['Cookie'] = webLoginCookie;
    if (webLoginUA) out['User-Agent'] = webLoginUA;
    return out;
  }

  function req(method, path, body, referer, headers) {
    return new Promise(function (resolve, reject) {
      var gap = Date.now() - lastReq;
      var wait = gap < POLITE_MS ? POLITE_MS - gap : 0;
      setTimeout(function () {
        var id = ++seq;
        waiting[id] = { resolve: resolve, reject: reject };
        try {
          var hj = JSON.stringify(overrideHeaders(path, headers));
          if (typeof Android.reqJxufe === 'function') {
            Android.reqJxufe(id, method, path, body || '', referer || BASE, hj);
          } else {
            delete waiting[id];
            reject(new Error('当前 App 版本不支持江财数据层，请先升级'));
          }
        } catch (e) {
          delete waiting[id];
          reject(new Error('原生请求失败：' + e));
        }
        lastReq = Date.now();
      }, wait);
    });
  }

  /** 默认头：江财金智对 iframe 内页面做来源校验，缺 Referer/Sec-Fetch-Dest 会 405。 */
  function pageHeaders(extra) {
    var h = {
      'Sec-Fetch-Dest': 'iframe',
      'Sec-Fetch-Mode': 'navigate',
      'Sec-Fetch-Site': 'same-origin'
    };
    if (extra) Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
    return h;
  }
  function ajaxHeaders(extra) {
    var h = {
      'X-Requested-With': 'XMLHttpRequest',
      'Sec-Fetch-Dest': 'empty',
      'Sec-Fetch-Mode': 'cors',
      'Sec-Fetch-Site': 'same-origin'
    };
    if (extra) Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
    return h;
  }
  function form(obj) {
    return Object.keys(obj).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(obj[k] == null ? '' : obj[k]);
    }).join('&');
  }
  function b64utf8(s) {
    var bytes = new TextEncoder().encode(s);
    var bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  // ------------------------------------------------------------------ HTML 表格
  function cellText(td, splitBlocks) {
    var c = td.cloneNode(true);
    var brs = c.querySelectorAll('br');
    for (var i = 0; i < brs.length; i++) {
      brs[i].parentNode.replaceChild(document.createTextNode('\n'), brs[i]);
    }
    // 🔴 U97（2026-11-28）：教务把同一格里的多段上课信息放进多个 <div>。
    //    textContent 会把相邻 <div> 的内容**直接粘在一起**（如「…S304(麦庐园
    //    校区)计算社会科学」），块与块之间没有换行，课名就被吞进教室行里。
    //    课表解析时给每个块级元素前后补一个换行，下面的 courseFromCell 才能
    //    按行切干净。成绩 / 考试那些表不传这个开关，保持原来的单行文本。
    if (splitBlocks) {
      var blocks = c.querySelectorAll('div, p, table');
      for (var j = 0; j < blocks.length; j++) {
        var b = blocks[j];
        if (!b.parentNode) continue;
        b.parentNode.insertBefore(document.createTextNode('\n'), b);
        b.parentNode.insertBefore(document.createTextNode('\n'), b.nextSibling);
      }
    }
    return (c.textContent || '').replace(/[ \t\r\f\v\u00a0]+/g, ' ').trim();
  }
  function tablesOf(html, splitBlocks) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var ts = doc.querySelectorAll('table'), out = [];
    for (var i = 0; i < ts.length; i++) {
      var rows = [], rs = ts[i].rows;
      for (var r = 0; r < rs.length; r++) {
        var cells = [];
        for (var c = 0; c < rs[r].cells.length; c++) cells.push(cellText(rs[r].cells[c], splitBlocks));
        rows.push(cells);
      }
      out.push(rows);
    }
    return out;
  }
  function plainText(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    return (doc.body ? doc.body.textContent : '') || '';
  }
  function decodeEntities(s) {
    var d = document.createElement('div');
    d.innerHTML = s;
    return d.textContent || '';
  }

  // ------------------------------------------------------------------ 会话状态
  var info = { loginId: '', userCode: '', xn: '', xq: '', yearTermId: '', schoolCode: SCHOOL_CODE };
  var semestersCache = [];

  /** 最近一次 loadMainInfo 拿到的原文（截前 400 字），只用来在失败时说清原因。 */
  var lastMainBody = '';

  /** 取数时是否改用「页面里普通 ajax」那一套头（不带 Sec-Fetch-*）。
   *  默认 false。只在「网页登录会话第一次没被认」时短暂置真再试一次，
   *  避免把一件小事的开关扩散到所有取数路径上。 */
  var softHeaders = false;

  /** 把「教务到底回了什么」压成半句话，拼在错误提示后面。
   *
   *  为什么值得加：登录态明明有效却读不出数据，只有几种可能，而它们在界面上
   *  长得完全一样。这一句让下一次反馈直接指到具体那一种，不用再猜一轮。 */
  function bodyHint() {
    var b = lastMainBody || '';
    if (!b) return '（教务没有回应）';
    if (/name=["']password["']|cas\/login|login\.action/i.test(b)) {
      return '（登录已过期，请重新同步）';
    }
    if (b.charAt(0) === '{') return '（读取失败，请重新同步）';
    return '（教务暂时没有回应）';
  }

  function authFail(body) {
    return /name=["']password["']|cas\/login/i.test(body || '');
  }

  /** 取主信息：POST showYearTerm.action（UTF-8 JSON），失败兜底 SetMainInfo.jsp。 */
  async function loadMainInfo() {
    try {
      // `softHeaders` 只在一个很窄的场合为真：网页登录后抄来的会话第一次没被认，
      // 换一套「页面里普通 ajax」的头型再试一次（见 loginWithWebCookie）。
      var hh = softHeaders ? ajaxHeaders({ 'Origin': BASE })
                           : pageHeaders({ 'Origin': BASE });
      var r = await req('POST', '/jw/common/showYearTerm.action', '', '/frame/homes.action', hh);

      // 留一份原文摘要：登录态明明有效却读不出来时，这一句是唯一能分家的证据
      // （是「教务回了登录页」还是「回了别的东西」）。
      lastMainBody = String((r && r.body) || '').slice(0, 400);
      var d = JSON.parse(r.body || '{}');
      var got = {
        loginId: String(d.loginId || d.gh || ''),
        userCode: String(d.userCode || ''),
        xn: String(d.xn || ''),
        xq: String(d.xqM || d.xq || ''),
        // 入学年级（成绩接口要它才能翻历史学期）。有的部署不发，空着也不影响。
        rxnj: String(d.rxnj || d.rxnjMc || ''),
        yearTermId: String(d.jwUserYeartermId || ''),
        schoolCode: String(d.schoolCode || SCHOOL_CODE)
      };
      if (got.userCode) { info = got; return info; }
    } catch (e) { /* 走兜底 */ }

    var paths = ['/custom/js/SetMainInfo.jsp', '/frame/js/SetMainInfo.jsp', '/js/SetMainInfo.jsp'];
    for (var i = 0; i < paths.length; i++) {
      try {
        var rr = await req('GET', paths[i], '', '/frame/homes.action', pageHeaders());
        var t = rr.body || '';
        var m1 = /G_USER_CODE\s*=\s*['"]([^'"]+)['"]/.exec(t);
        var m2 = /G_LOGIN_ID\s*=\s*['"]([^'"]+)['"]/.exec(t);
        var m3 = /_currentXn\s*=\s*['"]([^'"]+)['"]/.exec(t);
        var m4 = /_currentXq\s*=\s*['"]([^'"]+)['"]/.exec(t);
        if (m1 || m2) {
          info = {
            loginId: m2 ? m2[1] : info.loginId,
            userCode: m1 ? m1[1] : '',
            xn: m3 ? m3[1] : info.xn,
            xq: m4 ? m4[1] : info.xq,
            yearTermId: info.yearTermId,
            schoolCode: SCHOOL_CODE
          };
          return info;
        }
      } catch (e0) { /* 下一个路径 */ }
    }
    return info;
  }

  /** 验证当前会话是否有效：能拿到 userCode 就算有效。 */
  async function sessionValid() {
    try {
      await loadMainInfo();
      return !!info.userCode;
    } catch (e) { return false; }
  }

  function semesterParts(id) {
    var m = /^(\d{4})-([012])$/.exec((id || '').trim());
    return m ? { xn: m[1], xq: m[2] } : { xn: '', xq: '' };
  }

  async function ttParams(semesterId) {
    var sp = semesterParts(semesterId);
    var xn = sp.xn || info.xn || '2026';
    var xq = sp.xq || info.xq || '0';
    if (!info.userCode) await loadMainInfo();
    return { xn: xn, xq: xq, xh: info.userCode || '' };
  }

  // ------------------------------------------------------------------ 学期列表
  async function semesters() {
    if (semestersCache.length) return semestersCache;
    try {
      var body = form({
        comboBoxName: 'StMsXnxqDxDesc', paramValue: '', isYXB: 0,
        isCDDW: 0, isXQ: 0, isDJKSLB: 0, isZY: 0
      });
      var r = await req('POST', '/frame/droplist/getDropLists.action', body,
                        '/student/xkjg.wdkb.jsp?menucode=S20301', ajaxHeaders({ 'Origin': BASE }));
      var arr = JSON.parse(r.body || '[]');
      var out = [], seen = {};
      for (var i = 0; i < arr.length; i++) {
        var sid = String(arr[i].code || ''), name = String(arr[i].name || '');
        var sp = semesterParts(sid);
        if (sp.xn && name && !seen[sid]) {
          seen[sid] = 1;
          out.push({ id: sid, name: name, xn: sp.xn, xq: sp.xq });
        }
      }
      if (out.length) { semestersCache = out; return out; }
    } catch (e) { /* 兜底课表页 option */ }

    try {
      var rr = await req('GET', '/student/xkjg.wdkb.jsp?menucode=S20301', '',
                         '/frame/homes.action', pageHeaders());
      var re = /<option[^>]*value="(\d{4}-[012])"[^>]*>([^<]*)<\/option>/g;
      var m, seen2 = {};
      while ((m = re.exec(rr.body || ''))) {
        if (!seen2[m[1]]) {
          seen2[m[1]] = 1;
          var sp2 = semesterParts(m[1]);
          semestersCache.push({ id: m[1], name: m[2].trim(), xn: sp2.xn, xq: sp2.xq });
        }
      }
    } catch (e0) { /* 空列表 */ }
    return semestersCache;
  }

  // ------------------------------------------------------------------ 学籍
  function tag(xml, name) {
    var m = new RegExp('<' + name + '>([\\s\\S]*?)</' + name + '>', 'i').exec(xml || '');
    return m ? decodeEntities(m[1]).trim() : '';
  }
  async function profile() {
    var r = await req('POST',
      '/STU_BaseInfoAction.do?hidOption=InitData&menucode_current=S10101', '',
      '/student/stu.xsxj.xjda.jbxx.html?menucode=S10101',
      ajaxHeaders({ 'Origin': BASE }));
    var xml = r.body || '';
    var p = {
      studentId: tag(xml, 'yhxh') || tag(xml, 'xh'),
      name: tag(xml, 'xm'), className: tag(xml, 'bjmc'),
      major: tag(xml, 'zymc'), college: tag(xml, 'yxb'),
      gender: tag(xml, 'xb'), nativePlace: tag(xml, 'sysf'),
      birthday: tag(xml, 'csrq'), admissionYear: tag(xml, 'rxnj'),
      counselor: tag(xml, 'fdy'), phone: tag(xml, 'dh'), email: tag(xml, 'dzyx')
    };
    Object.keys(p).forEach(function (k) { if (!p[k]) delete p[k]; });
    if (p.studentId) return p;
    throw new Error('学籍信息没读到，请稍后重试');
  }

  // ------------------------------------------------------------------ 课表
  var ROW_BASE_PERIOD = { '一': 1, '二': 3, '三': 6, '四': 8, '五': 10 };
  var ROOM_RE = /^(.*?)\s*[（(]\s*([^（）()]*校区)\s*[）)]\s*$/;
  // 🔴 U95④（2026-11-28）：原正则是
  //     /(\d{1,2})\s*(?:-\s*(\d{1,2}))?\s*(单|双)?\s*\[\s*(\d{1,2})\s*-\s*(\d{1,2})\s*\]/
  //     要求「数字-数字[节-节]」中间不能夹任何字。可教务那边同一套课表里，
  //     有的老师把周次写成「2-9周[3-4]」「第2-9周[3-4]」（样本 xskcb 里确实有
  //     「2-9周 []」这种带「周」字的写法），正则匹配不上 → weekText 空 →
  //     periods 退化成 [rowBase]、weeks 空 → 这门课整门漏掉。
  //     用户反馈的正是这个：周五 3-4 节「计算社会科学」前 9 周有、第 10 周起没了。
  //     放宽点：允许「第」「周」字、允许单双前后有空格、允许节次用 ~ 或 , 分隔。
  //     五组捕获保持不变（①起周 ②止周 ③单/双 ④起节 ⑤止节），下面代码不用改。
  var WEEK_PERIOD_RE = /第?\s*(\d{1,2})\s*(?:[-~至]\s*(\d{1,2}))?\s*周?\s*(单|双)?\s*周?\s*[\[【]\s*(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*[\]】]/;
  var ROOM_KW = /校区|教学楼|教室|大楼|[楼主][\dA-Z]|场[CT\d]|馆|实训/;

  function courseFromCell(cell, rowBase) {
    var lines = cell.split('\n').map(function (s) { return decodeEntities(s).trim(); })
                      .filter(function (s) { return s; });
    if (!lines.length) return null;
    var name = lines[0], code = '';
    var fontRe = /<font[^>]*>([\s\S]*?)<\/font>/i;   // 课程名优先 font（原始行保留标签时）
    var fm = fontRe.exec(cell);
    if (fm) name = decodeEntities(fm[1].replace(/<[^>]*>/g, '')).trim();
    var cm = /^\[([^\]]+)\]\s*(.*)$/.exec(name);
    if (cm) { code = cm[1]; name = cm[2].trim(); }

    var teacher = '', room = '', campus = '', weekText = '', parity = '';
    var weeks = [], periods = [];
    for (var i = 1; i < lines.length; i++) {
      var ln = lines[i];
      var wm = WEEK_PERIOD_RE.exec(ln);
      if (wm) {
        // 🔴 U97（2026-11-28）：同一格里可能有**好几行**周次 —— 同一门课分
        //    两段周上课，或两段周对应不同节次。原来每遇到一行就把 weeks /
        //    periods 清空重算，最后只剩最后一段，前面的全丢（症状正是
        //    「前 9 周有、第 10 周起没了」）。改成**累加去重**，每一行认出来
        //    的周次、节次都算数。
        weekText = weekText ? weekText + '\n' + ln : ln;
        var w0 = parseInt(wm[1], 10), w1 = wm[2] ? parseInt(wm[2], 10) : w0;
        if (w1 < w0) { var tmp = w0; w0 = w1; w1 = tmp; }
        // 🔴 组号别数错：这条正则的五个括号依次是
        //    ① 起始周 ② 结束周 ③ 单/双 ④ 起始节 ⑤ 结束节。
        //    2026-11-23 之前这里取的是 wm[3]/wm[4]，也就是把「单/双」当成
        //    「起始节」、把「起始节」当成「结束节」—— parseInt('') = NaN，
        //    于是 periods 恒为空、每门课的 slots 也是空，
        //    课表就是一片空白（用户截图：第 1 周 · 0 门课）。
        var p0 = parseInt(wm[4], 10), p1 = parseInt(wm[5], 10);
        if (!(p0 > 0)) { p0 = rowBase; p1 = rowBase; }
        if (!(p1 >= p0)) p1 = p0;
        // 单 / 双只约束**这一行**认出的周次，别把没标注的其它行一起过滤掉。
        var lineParity = wm[3] || '';
        if (lineParity) parity = lineParity;
        // 🔴 U95④：周次可能是**逗号分隔的多段**（如「1-9,11-17周[3-4]」），
        //    WEEK_PERIOD_RE 只会抓到一段，另一段整段丢。这里在这一行里再扫
        //    一遍，把所有「数字」「数字-数字」段都收进来。
        var segs = [], segRe = /(\d{1,2})\s*(?:[-~至]\s*(\d{1,2}))?/g, sm;
        // 只在「[ / 【」之前的那段周次描述里扫，别把节次「3-4」也当成周次。
        var weekPart = ln.split(/[\[【]/)[0];
        while ((sm = segRe.exec(weekPart)) !== null) {
          var a = parseInt(sm[1], 10), b = sm[2] ? parseInt(sm[2], 10) : a;
          if (a < 1 || a > 30 || b < 1 || b > 30) continue;
          if (b < a) { var t2 = a; a = b; b = t2; }
          segs.push([a, b]);
        }
        if (!segs.length) segs.push([w0, w1]);
        segs.forEach(function (seg) {
          for (var w = seg[0]; w <= seg[1]; w++) {
            if (lineParity === '单' && w % 2 === 0) continue;
            if (lineParity === '双' && w % 2 === 1) continue;
            if (weeks.indexOf(w) < 0) weeks.push(w);
          }
        });
        for (var p = p0; p <= p1; p++) {
          if (periods.indexOf(p) < 0) periods.push(p);
        }
      } else if (weekText && ROOM_KW.test(ln)) {
        var rm = ROOM_RE.exec(ln);
        if (rm) { room = rm[1].trim(); campus = rm[2].trim(); }
        else room = ln;
      } else if (!teacher) {
        teacher = ln;
      }
    }
    if (!weekText) {
      // 🔴 U97 兜底：周次一个字都没认出来时按**整学期**显示。app.js 那边是
      //    `c.weeks.indexOf(week) < 0` 过滤，空数组等于这门课每一周都消失 ——
      //    用户截图里周五那门课就是这样整门不见的。宁可多显示几周，也不能
      //    整门漏掉；周次写法再怪，课也得在课表上。
      periods = [rowBase];
      for (var fw = 1; fw <= 20; fw++) weeks.push(fw);
    } else {
      weeks.sort(function (a, b) { return a - b; });
      periods.sort(function (a, b) { return a - b; });
    }
    return {
      name: name, code: code, teacher: teacher, room: room, campus: campus,
      weeks: weeks, parity: parity, periods: periods
    };
  }

  async function timetable(semesterId) {
    var p = await ttParams(semesterId);
    if (!p.xh) throw new Error('教务登录已过期，请重新登录');
    var params = b64utf8('xn=' + p.xn + '&xq=' + p.xq + '&xh=' + p.xh);
    var r = await req('GET', '/student/wsxk.xskcb10319.jsp?params=' + encodeURIComponent(params),
                      '', '/frame/homes.action', pageHeaders());
    var html = r.body || '';
    if (authFail(html)) { var e = new Error('登录已过期'); e.kind = 'auth'; throw e; }
    if (html.indexOf('课表') < 0 && html.indexOf('mytable') < 0) throw new Error('课表读取失败');

    // U97：课表格子里常有并列的 <div>（同一门课分两段周），要按块换行拆开；
    // 其它表（成绩 / 选课清单）仍用原来的单行文本。
    var tables = tablesOf(html, true);
    var grid = null;
    for (var t = 0; t < tables.length; t++) {
      var rows = tables[t];
      for (var ri = 0; ri < rows.length; ri++) {
        if (rows[ri].length >= 2 &&
            rows[ri].slice(0, 2).join(' ').indexOf('星期一') >= 0) { grid = rows; break; }
      }
      if (grid) break;
    }
    if (!grid) grid = tables[1] || [];

    var courses = [], seenMap = {}, maxWeek = 0;
    for (var r1 = 0; r1 < grid.length; r1++) {
      var row = grid[r1];
      if (!row.length || row.join(' ').indexOf('星期') >= 0) continue;
      var label = '';
      for (var k = 0; k < row.length && k < 2; k++) {
        if (ROW_BASE_PERIOD[row[k].trim()]) { label = row[k].trim(); break; }
      }
      var base = ROW_BASE_PERIOD[label] || 1;
      var cells = row.filter(function (c) { return c != null; });
      var days = cells.length >= 9 ? cells.slice(2, 9) : cells.slice(-7);
      if (days.length > 7) days = days.slice(0, 7);
      for (var d = 0; d < days.length; d++) {
        if (!days[d]) continue;
        var co = courseFromCell(days[d], base);
        if (!co || !co.name) continue;
        var key = [co.name, co.teacher, co.room, co.weeks.join(','), co.parity].join('|');
        var slotWeeks = co.periods.map(function (per) { return { day: d, period: per - 1 }; });
        if (seenMap[key]) {
          var ex = seenMap[key];
          for (var si = 0; si < slotWeeks.length; si++) {
            var sw = slotWeeks[si];
            if (!ex.slots.some(function (s0) { return s0.day === sw.day && s0.period === sw.period; })) {
              ex.slots.push(sw);
            }
          }
        } else {
          co.slots = slotWeeks;
          seenMap[key] = co;
          courses.push(co);
        }
        co.weeks.forEach(function (w) { if (w > maxWeek) maxWeek = w; });
      }
    }
    courses.sort(function (a, b) {
      var ap = a.slots[0] || { day: 99, period: 99 }, bp = b.slots[0] || { day: 99, period: 99 };
      return (ap.day - bp.day) || (ap.period - bp.period);
    });

    var xqName = { '0': '第一学期', '1': '第二学期', '2': '第二阶段' }[p.xq] || '';
    return {
      unitCount: 12, totalWeeks: maxWeek || 20, weekStart: 1,
      semester: p.xn + '-' + (parseInt(p.xn, 10) + 1) + '学年' + xqName,
      courses: courses
    };
  }

  // ------------------------------------------------------------------ 成绩
  /** 「2025-2026学年第一学期」→ { year:'2025-2026', term:1 }。
   *
   *  成绩表每页顶上就写着这一句，是**唯一可靠**的学期来源 ——
   *  我们请求里带的 xn/xq 有可能被服务端改写（实测就出现过：按 2026 第一学期
   *  去要，回来的却是上一学年），所以学期名以它自己写的为准。 */
  function termKeyParts(label) {
    // 「第 1 学期 / 第一学期 / 第二阶段」都要认：江财的成绩页会把暑期/实践周
    // 单独印成一张「第二学期 第二阶段」的表（样例里就有），认不出来它就会被
    // 塞进错误的学期里。阶段并入同一个学期的学期号 —— 界面上只有大几上/下，
    // 没有「第几阶段」这一档。
    var m = /(\d{4})\s*-\s*(\d{4})\s*学年\s*第?\s*([一二12])\s*(?:学期|阶段)/.exec(String(label || ''));
    if (!m) return null;
    return {
      year: m[1] + '-' + m[2],
      term: (m[3] === '一' || m[3] === '1') ? 1 : 2
    };
  }

  /** 把一页成绩 HTML 里的**每一张**成绩表都解出来。
   *
   *  🔴 2026-11-23：用户「成绩只读取到了大一上」。原因是这里以前只认**第一张**
   *     成绩表（`target` 找到就 break）—— 而教务在「不限定学期」查询时，
   *     会把每个学期的成绩各印成一张表、上面各带一句「学年学期：…」。
   *     于是后面几个学期全被丢掉，界面上就只剩一个学期。
   *     现在按文档顺序遍历：遇到写着「学年学期」的小表就记下它当**下一张表的名字**，
   *     遇到成绩表就带着这个名字收下。返回 [{ label, rows }]。 */
  function parseGradeHtml(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var ts = doc.querySelectorAll('table');
    var label = '';
    var out = [];
    for (var i = 0; i < ts.length; i++) {
      var t = ts[i];
      var rs = t.rows;
      if (!rs || !rs.length) continue;
      var headTxt = '';
      for (var q = 0; q < rs.length && q < 2; q++) {
        headTxt += ' ' + (rs[q].textContent || '');
      }
      var lm = /学年学期[：:]\s*([^\s]+)/.exec(headTxt.replace(/\s+/g, ' '));
      if (lm && rs.length <= 2) { label = lm[1]; continue; }
      if (!lm) {
        // 有的部署把「学年学期」塞在表格外面的 <div> 里，只有一行表头时也得认
        var pm = /学年学期[：:]\s*([^\s]+)/.exec(String(t.previousElementSibling
          ? t.previousElementSibling.textContent : '').replace(/\s+/g, ' '));
        if (pm) label = pm[1];
      }

      var head = [];
      for (var c0 = 0; c0 < rs[0].cells.length; c0++) head.push(cellText(rs[0].cells[c0]));
      var hs = head.join(' ');
      if (hs.indexOf('课程') < 0 || hs.indexOf('成绩') < 0) continue;

      var idx = {}, head2 = head;
      head2.forEach(function (h, j) {
        var s = h.trim();
        if (s.indexOf('课程') >= 0 && idx.name == null) idx.name = j;
        else if (s === '学分') idx.credit = j;
        else if (s === '成绩') idx.score = j;
        else if (s.indexOf('学分绩点') >= 0) idx.creditGpa = j;
        else if (s.indexOf('绩点') >= 0 && idx.gpa == null) idx.gpa = j;
        else if (s.indexOf('获得学分') >= 0) idx.earned = j;
        else if (s.indexOf('修读性质') >= 0) idx.nature = j;
        else if (s.indexOf('考核') >= 0) idx.form = j;
        else if (s.indexOf('类别') >= 0) idx.category = j;
      });
      if (idx.name == null || idx.score == null) continue;

      var rows = [];
      for (var ri = 1; ri < rs.length; ri++) {
        var cells = [];
        for (var c1 = 0; c1 < rs[ri].cells.length; c1++) cells.push(cellText(rs[ri].cells[c1]));
        if (!cells.length || cells.join('').trim() === '') continue;
        var rawName = String(cells[idx.name] || '').trim();
        if (!rawName) continue;
        var cm = /^\[([^\]]+)\]\s*(.*)$/.exec(rawName);
        rows.push({
          code: cm ? cm[1] : '',
          name: cm ? cm[2].trim() : rawName,
          credit: idx.credit != null ? String(cells[idx.credit] || '').trim() : '',
          score: String(cells[idx.score] || '').trim(),
          gpa: idx.gpa != null ? String(cells[idx.gpa] || '').trim() : '',
          creditGpa: idx.creditGpa != null ? String(cells[idx.creditGpa] || '').trim() : '',
          earned: idx.earned != null ? String(cells[idx.earned] || '').trim() : '',
          nature: idx.nature != null ? String(cells[idx.nature] || '').trim() : '',
          form: idx.form != null ? String(cells[idx.form] || '').trim() : '',
          category: idx.category != null ? String(cells[idx.category] || '').trim() : ''
        });
      }
      if (rows.length) out.push({ label: label, rows: rows });
    }
    return out;
  }

  /** 查一次成绩接口，返回该页里**所有**学期组 [{ label, rows }]。
   *  xn / xq 传空串 = 让教务不要把结果限定在某一学期（会一次给出全部学期）。 */
  async function gradeOnce(p, xn, xq, admission) {
    var data = {
      xn: xn, xn1: /^\d+$/.test(xn) ? String(parseInt(xn, 10) + 1) : '',
      xq: xq, xh: p.xh,
      ysyxS: 'on', sjxzS: 'on', ysyx: 'yxcj', sjxz: 'sjxz1',
      zx: '1', fx: '0', wz: '0',
      rxnj: admission, nj: admission, zxC: 'on', xsjd: '1'
    };
    var r = await req('POST', '/student/xscj.stuckcj_data10421.jsp', form(data),
                      '/student/xscj.stuckcj.jsp?menucode=S40303',
                      pageHeaders({ 'Origin': BASE }));
    var html = r.body || '';
    if (authFail(html)) { var e = new Error('登录已过期'); e.kind = 'auth'; throw e; }
    if (html.indexOf('成绩') < 0) return [];
    return parseGradeHtml(html);
  }

  /** 要查哪些学期：从入学那一学年排到当前学期，一年两个学期。
   *
   *  🔴 为什么不向教务要「一次性全部」（2026-11-23，用户：「成绩只读到了
   *     一部分」）：那个参数没有文档，翻了页面也只有一个学年下拉，猜错就是
   *     静默空表。按学年一个个问是确定的 —— 四年也就八个请求，而且哪个学期
   *     没数据就是空表，不会连带别的学期一起丢。
   */
  function gradeTermList(admission) {
    var nowY = parseInt(info.xn, 10) || new Date().getFullYear();
    var y0 = parseInt(admission || '', 10);
    // 入学年拿不到就往前退四年：多查几个空学期不花钱，漏掉真数据才要命
    if (!(y0 >= 1990 && y0 <= nowY + 1)) y0 = nowY - 3;
    var out = [];
    for (var y = y0; y <= nowY; y++) {
      // 🔴 一年两个学期**都问**，不按「现在读到第几学期」提前收手：
      //    课表的 xq 是 0 起的（0=第一学期），成绩的 xq 是 1 起的，
      //    拿课表那个数去截断会把整学期漏掉。多问一个空学期只多花一秒。
      out.push({ xn: String(y), xq: '1' });
      out.push({ xn: String(y), xq: '2' });
    }
    return out;
  }

  async function grades(semesterId) {
    var p = await ttParams(semesterId);
    // 入学年级：showYearTerm 里有就直接用（省一次请求），没有才去学籍页捞。
    var admission = String(info.rxnj || '');
    if (!admission) {
      try { admission = (await profile()).admissionYear || ''; } catch (e) { /* 兜底 */ }
    }
    if (!admission) admission = String((parseInt(p.xn, 10) || 2026) - 1);

    var plan = semesterId
      ? [{ xn: p.xn, xq: p.xq }]
      : gradeTermList(admission);

    // 🔴 2026-11-23：先要一次「不限定学期」的。教务在那种查询下会把每个学期的
    //    成绩各印一张表，一次就能拿全（这是用户说「只读到大一上」的正解）。
    //    它要是不认空参数（返回空），下面的逐学期循环照样兜得住。
    var groups = [];
    if (!semesterId) {
      try {
        groups = await gradeOnce(p, '', '', admission);
      } catch (e0) {
        if (e0 && e0.kind === 'auth') throw e0;
      }
    }

    var buckets = {}, order = [];
    var totCredit = 0, totEarned = 0, totCp = 0;

    /** 把一组 {label, rows} 并进它所属学期的桶里。
     *
     *  🔴 不能像以前那样「见过这个学期就整组丢掉」（2026-11-23）。
     *     教务那一页会把一张长表**拆成好几张 table**（分页/分块），它们带着
     *     同一个「学年学期」标题 —— 丢掉第二张就等于丢掉后面那几门课，
     *     表现正是「成绩只读到一部分」。所以改成**并桶 + 按课去重**。 */
    var put = function (got, reqXn, reqXq) {
      if (!got || !got.rows.length) return;
      var kp = termKeyParts(got.label);
      // 学期名认不出来就按请求参数拼一个，总比丢掉这学期强
      var year = kp ? kp.year : (String(reqXn) + '-' + (parseInt(reqXn, 10) + 1));
      var term = kp ? kp.term : (parseInt(reqXq, 10) || 1);
      var key = year + ' ' + term;
      if (!buckets[key]) { buckets[key] = { year: year, term: term, rows: [], seen: {} }; order.push(key); }
      var b = buckets[key];
      got.rows.forEach(function (c) {
        var ck = (c.code || '') + '|' + c.name;
        if (b.seen[ck]) return;                  // 同一门课在同一学期里印了两遍（重修/替代）
        b.seen[ck] = 1;
        b.rows.push(c);
      });
    };

    // 先收「一次拿全」的那批
    groups.forEach(function (g) { put(g, p.xn, p.xq); });

    // 再按学期逐个补：一是兜住「空参数不认」的部署，二是补上「全量」里
    // 可能缺的学期（有些部署只给它认定的「当前学年」）。
    // 🔴 但如果上面那次已经拿回**不止一个学期**，说明这个部署本来就不看 xn/xq，
    //    再逐个学期问一遍只是把同一份数据重取四次（每次还要等 1.2 秒）。
    for (var i = 0; groups.length <= 1 && i < plan.length; i++) {
      var gl;
      try {
        gl = await gradeOnce(p, plan[i].xn, plan[i].xq, admission);
      } catch (e1) {
        if (e1 && e1.kind === 'auth') throw e1;    // 登录没了就别继续糟蹋请求
        continue;
      }
      var rxXn = plan[i].xn, rxXq = plan[i].xq;
      (gl || []).forEach(function (g) { put(g, rxXn, rxXq); });
    }

    // 桶 → 课程 + 每学期汇总
    var courses = [], summary = [];
    order.forEach(function (key) {
      var b = buckets[key];
      if (!b.rows.length) return;
      var cSum = 0, cpSum = 0, eSum = 0;
      b.rows.forEach(function (c) {
        c.term = key;
        courses.push(c);
        var cr = parseFloat(c.credit) || 0;
        var ea = parseFloat(c.earned) || 0;
        var gp = parseFloat(c.gpa);
        var cp = parseFloat(c.creditGpa);
        cSum += cr;
        eSum += ea;
        // 学分绩点这一列就是 绩点×学分，直接用它算平均，别自己再乘一遍
        if (!isNaN(cp)) cpSum += cp;
        else if (!isNaN(gp)) cpSum += gp * cr;
      });
      totCredit += cSum;
      totEarned += eSum;
      totCp += cpSum;
      summary.push({
        year: b.year, term: b.term,
        gpa: cSum > 0 ? (cpSum / cSum).toFixed(2) : '',
        credits: cSum.toFixed(1).replace(/\.0$/, ''),
        count: b.rows.length
      });
    });

    summary.sort(function (a, b) {
      return (parseInt(a.year, 10) - parseInt(b.year, 10)) || (a.term - b.term);
    });
    courses.sort(function (a, b) {
      var ap = String(a.term).split(' '), bp = String(b.term).split(' ');
      var d = (parseInt(ap[0], 10) - parseInt(bp[0], 10)) || (parseInt(ap[1], 10) - parseInt(bp[1], 10));
      return d;
    });

    return {
      summary: summary,
      overall: courses.length ? {
        gpa: totCredit > 0 ? (totCp / totCredit).toFixed(2) : '',
        // 「总学分」按**已获得**算（含重修只算一次是教务的事）；没有这一列就退回计划学分
        credits: (totEarned > 0 ? totEarned : totCredit).toFixed(1).replace(/\.0$/, ''),
        count: courses.length
      } : null,
      courses: courses,
      empty: courses.length === 0,
      gpaScale: 5.0
    };
  }

  // ------------------------------------------------------------------ 考试
  async function examRounds() {
    var r = await req('GET', '/student/data/classPath.xml', '', '/frame/homes.action', pageHeaders());
    var xml = r.body || '';
    var m = /<examArrangeInfo>([\s\S]*?)<\/examArrangeInfo>/i.exec(xml);
    if (!m) return [];
    var cp = m[1].trim();
    if (!cp) return [];
    var rr = await req('POST', '/STU_DynamicInitDataAction.do?classPath=' + encodeURIComponent(cp), '',
                       '/student/ksap.ksapb.html?menucode=S20403',
                       ajaxHeaders({ 'Origin': BASE }));
    var x2 = rr.body || '';
    var out = [], re = /<item>([\s\S]*?)<\/item>/gi, im;
    while ((im = re.exec(x2))) {
      var id = tag(im[1], 'kslcdm'), name = tag(im[1], 'kslcmc');
      if (id || name) out.push({ id: id, name: name });
    }
    return out;
  }

  async function exams(semesterId) {
    var p = await ttParams(semesterId);
    var rounds = [];
    try { rounds = await examRounds(); } catch (e) { /* 无轮次也查一次 */ }
    var list = rounds.length ? rounds : [{ id: '' }];
    var all = [], seen = {};
    for (var i = 0; i < list.length; i++) {
      var data = { xn: p.xn, xq: p.xq, xh: p.xh, xxlc: list[i].id || '' };
      var r = await req('POST', '/student/ksap.ksapb_date.jsp', form(data),
                        '/student/ksap.ksapb.html?menucode=S20403', pageHeaders({ 'Origin': BASE }));
      var html = r.body || '';
      if (authFail(html)) continue;
      if (html.indexOf('没有检索到记录') >= 0) continue;
      var tables = tablesOf(html), target = null;
      for (var ti = 0; ti < tables.length; ti++) {
        var f = (tables[ti][0] || []).join(' ');
        if (f.indexOf('考试') >= 0 || f.indexOf('课程') >= 0 || f.indexOf('地点') >= 0) {
          target = tables[ti]; break;
        }
      }
      if (!target) continue;
      var head = target[0], idx = {};
      head.forEach(function (h, j) {
        var s = h.trim();
        if (s === '课程' || s === '课程名称') idx.name = j;
        else if (s === '课程序号') idx.code = j;
        else if (s === '考试类别') idx.category = j;
        else if (s === '考试形式') idx.form = j;
        else if (s === '考试日期') idx.date = j;
        else if (s === '考试时间') idx.time = j;
        else if (s === '考试地点' || s === '考场') idx.room = j;
        else if (s === '座位号' || s === '考场座位号') idx.seat = j;
        else if (s === '考试情况') idx.status = j;
        else if (s === '备注') idx.note = j;
      });
      if (idx.name == null) continue;
      for (var ri = 1; ri < target.length; ri++) {
        var row = target[ri];
        if (!row.join('').trim()) continue;
        var name = (row[idx.name] || '').trim();
        if (!name) continue;
        var o = {
          name: name, code: idx.code != null ? row[idx.code] : '',
          category: idx.category != null ? row[idx.category] : '',
          form: idx.form != null ? row[idx.form] : '',
          date: idx.date != null ? row[idx.date] : '',
          time: idx.time != null ? row[idx.time] : '',
          room: idx.room != null ? row[idx.room] : '',
          seat: idx.seat != null ? row[idx.seat] : '',
          status: idx.status != null ? row[idx.status] : '',
          note: idx.note != null ? row[idx.note] : ''
        };
        var key = [o.name, o.date, o.room].join('|');
        if (!seen[key]) { seen[key] = 1; all.push(o); }
      }
    }
    return { exams: all, published: all.length > 0, batches: rounds, semesters: semestersCache };
  }

  // ------------------------------------------------------------------ 同步入口
  async function syncOnce() {
    await loadMainInfo();
    if (!info.userCode) { var e = new Error('登录已失效，请重新登录'); e.kind = 'auth'; throw e; }
    var sem = await semesters();
    var p = await profile();
    var tt = await timetable('');
    var gr = await grades('');
    var ex = await exams('');
    var current = sem.length ? sem[sem.length - 1] : { name: tt.semester };
    return {
      ok: true, syncedAt: Date.now(),
      profile: p, semester: current,
      unitCount: tt.unitCount, totalWeeks: tt.totalWeeks,
      courses: tt.courses, grades: gr, exams: ex
    };
  }

  // ------------------------------------------------------------------ 学号密码登录
  /**
   * 江财的账号体系是 ssl.jxufe.edu.cn 上的标准 CAS（统一身份认证）。
   *
   * 🔴🔴 2026-11-23：**别再走 jwxt 自己的 /cas/logon.action**。
   *    那条路（getTempDeskey → strEnc(3DES) → token=md5(md5(params)+md5(now))）
   *    是金智的本地登录，学校已经关了：服务端固定回
   *    `{"status":"410","message":"请通过统一身份认证平台登录！"}`。
   *    App 里那句「登录未成功，请稍后再试」就是它 —— strEnc 那套实现本身
   *    和学校 JS 对拍过三次全一致，只是对面不再收这张表单。
   *
   * 现在的路（纯 java.net，不需要 WebView）：
   *    ① GET  ssl.jxufe.edu.cn/cas/login?service=<教务的 cas 落点>
   *       → 拿到本次会话的 execution（一个几百字节的签名串，必须原样回传）
   *    ② POST 同一个地址：username / password / captcha / currentMenu=1 /
   *       failN=-1 / mfaState / execution / _eventId=submit
   *    ③ CAS 认证通过 → 302 带 ticket 回 https://jwxt.jxufe.edu.cn/cas/login.action
   *       → 教务校验 ticket 后发 JSESSIONID，重定向到 /frame/homes.action
   *       整条链由 HttpURLConnection 自动跟随，cookie 由全局 CookieStore 接住。
   *
   * 🔴 service 参数不能瞎拼：用教务登录页上 caslogin 那个落点
   *    `/cas/login.action`（已实测 CAS 认这个 service，返回的是登录表单而不是
   *    「未注册的服务」）。
   * 🔴 这个方法**不再用 strEnc / hex_md5**，那两个函数留着是因为
   *    jxufe_eams.py 的对拍脚本还在用，删了会连带删掉一份已验证的移植。
   */
  var CAS_ROOT = 'https://ssl.jxufe.edu.cn';
  var CAS_SERVICE = BASE + '/cas/login.action';

  /** CAS 要的是普通导航头。**不能带 pageHeaders 的 Sec-Fetch-Dest: iframe**：
   *  那是给教务 iframe 页面过的校验，套到 CAS 上会被当成嵌入式请求。 */
  function casHeaders(extra) {
    var h = { 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' };
    if (extra) Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
    return h;
  }

  /** CAS 表单登录。成功返回 {ok:true}，失败带 kind 让界面说人话。 */
  async function casLogin(username, password) {
    // 走 CAS 这条路时，网页那一套原样转发必须撤掉：票是新换的，
    // 再捎带上一份旧的教务 cookie 只会让服务端更疑惑。
    webLoginCookie = '';
    webLoginUA = '';
    var loginUrl = CAS_ROOT + '/cas/login?service=' + encodeURIComponent(CAS_SERVICE);
    var page;
    try {
      // referer 传空：这是跨站到 CAS，带教务的 Referer 反而怪。
      page = await req('GET', loginUrl, '', '', casHeaders());
    } catch (e) {
      return { ok: false, error: '连不上统一身份认证，请检查网络后重试', kind: 'net' };
    }
    var html = (page && page.body) || '';
    var m = /name="execution"\s+value="([^"]+)"/.exec(html);
    if (!m) {
      // 有可能这一跳已经带着有效会话进了教务（cookie 还在）
      if (/frame\/homes|homes\.action/.test(html)) return { ok: true };
      return { ok: false, error: '统一身份认证没给登录页，请稍后再试', kind: 'page' };
    }

    var body = form({
      username: username,
      password: password,
      captcha: '',
      currentMenu: '1',
      failN: '-1',
      mfaState: '',
      execution: m[1],
      _eventId: 'submit',
      geolocation: '',
      fpVisitorId: '',
      trustAgent: ''
    });

    var r;
    try {
      r = await req('POST', loginUrl, body, loginUrl, casHeaders({
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': CAS_ROOT
      }));
    } catch (e2) {
      return { ok: false, error: '连不上统一身份认证，请检查网络后重试', kind: 'net' };
    }

    var raw = (r && r.body) || '';
    var landed = (r && r.url) || '';
    // 成功：已经落在教务域名下，而且不再是 CAS 登录页
    if (landed.indexOf('jwxt.jxufe.edu.cn') >= 0 &&
        landed.indexOf('/cas/login?service') < 0) {
      return { ok: true };
    }
    // 实测（2026-11-23，用不存在的学号打过一次）：密码这一步过了之后，
    // 学校固定要求**双因子验证**，返回页里的 errors 就是「双因子验证失败」，
    // 页面里还写着「安全手机未绑定，无法使用安全手机验证」。
    // ⇒ 纯密码登录注定过不去，必须让用户在内置网页里把人脸/短信/扫码过掉。
    if (/双因子|二次验证|安全手机|身份验证|扫码|动态码|mfa/i.test(raw)) {
      return { ok: false, kind: 'mfa',
               error: '学校要二次验证：点下面「用喜鹊儿扫码登录」，登完回来自动读取' };
    }
    if (/验证码/.test(raw) && /请输入|输入验证码|请填写/.test(raw)) {
      return { ok: false, kind: 'captcha',
               error: '学校这次要输入验证码：点下面「用喜鹊儿扫码登录」，登完回来自动读取' };
    }
    if (/密码错误|用户名或密码|账号或密码|认证失败|用户名不存在/i.test(raw)) {
      return { ok: false, error: '学号或密码不对', kind: 'auth' };
    }
    return { ok: false, error: '登录没成功，请稍后再试', kind: 'page' };
  }

  async function loginWithPassword(username, password) {
    username = String(username == null ? '' : username).trim();
    password = String(password == null ? '' : password);
    if (!username || !password) {
      return { ok: false, error: '请填学号和密码', kind: 'auth' };
    }
    // 密码这条路自己会走 CAS，网页登录留下的原样转发不能混进来。
    webLoginCookie = '';
    webLoginUA = '';

    // ⓞ 先看看是不是已经有会话：内置网页里登录过、或上次的 cookie 还没过期。
    //    这条先跑，能省掉一次 CAS 往返，也让「网页登录后回到 App」自然生效。
    try {
      info.userCode = '';
      await loadMainInfo();
    } catch (e0) { /* 没会话就是没会话，往下走 CAS */ }

    if (!info.userCode) {
      var cr = await casLogin(username, password);
      if (!cr.ok) return cr;
      info.userCode = '';
      try {
        await loadMainInfo();
      } catch (e1) {
        return { ok: false, error: '登录没有完成，请稍后再试', kind: 'net' };
      }
      if (!info.userCode) {
        return { ok: false, error: '登录过了但教务没认，请稍后再试', kind: 'page' };
      }
    }

    var saved = '';
    try { saved = String(Android.eamsCookieSave('') || ''); } catch (e7) { saved = 'fail: ' + e7; }
    return { ok: true, username: info.loginId || username, saved: saved };
  }

  // ------------------------------------------------------------------ Cookie 注入
  /** 解析浏览器复制的 cookie 串 / cURL / JSON，返回 [{name,value,domain,path}] */
  function parseCookie(raw) {
    var s = (raw || '').trim(), jar = [];
    if (!s) return jar;
    // JSON 数组（原生 dump 格式）
    if (s.charAt(0) === '[') {
      try { return JSON.parse(s); } catch (e) { /* 当普通串处理 */ }
    }
    // cURL: -b 'a=1; b=2' 或 --cookie "..."
    var bm = /(?:-b|--cookie)\s+['"]([^'"]+)['"]/.exec(s);
    if (bm) s = bm[1];
    // 去掉 "cookie:" 前缀
    s = s.replace(/^\s*cookie\s*:\s*/i, '').trim();
    s.split(';').forEach(function (part) {
      var eq = part.indexOf('=');
      if (eq <= 0) return;
      var name = part.slice(0, eq).trim(), value = part.slice(eq + 1).trim();
      if (name) jar.push({ name: name, value: value, domain: 'jwxt.jxufe.edu.cn', path: '/' });
    });
    return jar;
  }

  /** 注入 Cookie 到 Java 全局 CookieManager，并验证会话；成功加密落盘。 */
  async function loginWithCookie(raw) {
    var jar = parseCookie(raw);
    if (!jar.some(function (c) { return c.name === 'JSESSIONID'; })) {
      return { ok: false, error: '没找到 JSESSIONID，请复制完整的 cookie 行' };
    }
    try {
      var ok = Android.eamsCookieSet(JSON.stringify(jar));
      if (ok === false) return { ok: false, error: '登录没有完成，请重试' };
    } catch (e) {
      return { ok: false, error: '这个版本暂不支持网页登录，请更新后用' };
    }
    info.userCode = '';
    await loadMainInfo();
    if (!info.userCode) {
      // 文案别再提「复制 cookie」：这个 App 里没有粘贴 cookie 的入口，
      // 用户看到只会去找一个不存在的按钮。现在只有两条真出路 ——
      // 密码重登，或者「在网页里登录一次」。
      // 网页登录那条路会把整行 cookie **原样**当请求头发出去（见 overrideHeaders），
      // 所以走到这里还失败，问题多半不在 cookie 本身，得把教务的回复带出来。
      var hint = webLoginCookie ? bodyHint() : '';
      return { ok: false, error: '这个登录态已经过期了，请再登录一次' + hint, kind: 'auth' };
    }

    var saved = '';
    try { saved = String(Android.eamsCookieSave(raw) || ''); } catch (e) { saved = 'fail: ' + e; }
    return { ok: true, username: info.loginId, saved: saved };
  }

  /** 网页那边对「会话到底通不通」的自检结论，拼成一句人话。
   *
   *  为什么需要（2026-11-23，用户：「每次都扫码进去，但是还是读取不出来」）：
   *  网页里登进去了、回 App 读不出来，只有两种可能 —— 网页里其实也没登进教务，
   *  或者网页里是通的、搬 cookie 这一步丢了。两者在界面上长得一模一样，
   *  这句提示就是用来分家的，不然只能在「没登上 / 搬丢了」之间瞎猜。 */
  function probeNote() {
    var p = '';
    try { p = String(Android.eamsWebProbe() || ''); } catch (e) { p = ''; }
    if (!p) return '';
    if (p.indexOf('userCode') >= 0) {
      return '（网页里已经登录成功，请回到 App 重试）';
    }
    if (/^S\d+:</.test(p) && /login|登录|login\.action|<html/i.test(p)) {
      return '（网页里自检也没过：那个页面里其实还没真正登进教务）';
    }
    return '';
  }

  /** 从 App 内置网页的 CookieManager 里把教务会话捞过来。
   *
   *  为什么需要这一条：统一身份认证可能要走**二次验证 / 验证码 / 扫码**，
   *  那几步是给人做的，脚本不该硬闯（会触发风控，而且今天破了明天就失效）。
   *  做法是让用户在 App 内置网页里正常登录一次，回到 App 把 WebView 里的
   *  cookie 抄进 java.net 的罐子 —— 之后取数全走这一套，和密码登录没有区别。
   *  🔴 WebView 的 cookie 存在 android.webkit.CookieManager 里，和 java.net 的
   *     CookieStore 是**两个罐子**，所以必须有原生这一跳（eamsCookieFromWebView）。 */
  async function loginWithWebCookie() {
    if (typeof Android === 'undefined' ||
        typeof Android.eamsCookieFromWebView !== 'function') {
      return { ok: false, error: '当前 App 版本不支持读网页登录态，请先升级' };
    }
    var raw = '';
    try {
      raw = String(Android.eamsCookieFromWebView() || '');
    } catch (e) {
      return { ok: false, error: '读不到网页里的登录态' };
    }

    var res;
    if (raw.indexOf('JSESSIONID') >= 0) {
      // ⓐ 网页已经落到了教务：直接抄 JSESSIONID，同时把整行原样转发 + 对齐 UA。
      webLoginCookie = raw;
      try { webLoginUA = String(Android.webUa ? Android.webUa() : ''); } catch (e0) { webLoginUA = ''; }
      res = await loginWithCookie(raw);
      if (!res.ok) {
        // 🔴 第二次机会：同一张会话，网页里 `fetch` 一下就通，App 这边却说过期。
        //    App 带的是专门给老接口准备的 `Sec-Fetch-Dest: iframe / Mode: navigate`，
        //    而网页里那次是浏览器自己生成的 `cors / empty`。学校一旦换了网关校验，
        //    差的就是这几个头 —— 换成网页那套再试一次，成本只有几秒。
        softHeaders = true;
        var r2;
        try { r2 = await loginWithCookie(raw); } finally { softHeaders = false; }
        if (r2 && r2.ok) return r2;
        res = r2 || res;
        // 抄过来的是死会话（常见于用户点「完成」点早了、或者打转把票烧掉了）——
        // 下面还有一条更结实的路，试完它再认输。
        var t1 = await casTicketExchange();
        if (t1.ok) return t1;
      }
    } else {
      // ⓑ 网页只走完了统一身份认证，还没落到教务。
      //    CAS 的身份证（CASTGC）在 **ssl.jxufe.edu.cn** 上，不在教务域 ——
      //    只问 jwxt 一条当然拿不到东西。这条分支就是为它准备的。
      res = await casTicketExchange();
    }

    if (res && !res.ok && res.error) res.error += probeNote();
    return res;
  }

  /** 用网页里留下的 CAS 票，自己走一次换票进教务。
   *
   *  为什么要有这一条（2026-11-23，用户：「我登陆完成回来之后也没有用啊」）：
   *  用户在内置网页里登录时，CAS 认可之后会 302 到
   *  `jwxt.jxufe.edu.cn/cas/login.action?ticket=ST-…`。**这张票是一次性的**，
   *  而 WebActivity 那套「来回跳就重载」的兜底一旦触发，票就被烧在网页那边了：
   *  WebView 罐子里只剩 ssl 上的 CASTGC，教务域的 JSESSIONID 始终没建出来。
   *  此时「抄 jwxt 的 cookie」必然是空 —— 但 CASTGC 还在，我们自己换一次票就行。
   *
   *  做法和 casLogin() 的第三步同源：把 CASTGC 倒进 java.net 罐子，
   *  再用普通 GET 走一次 `cas/login?service=…`，剩下的重定向
   *  （发票 → 教务校验 → 发 JSESSIONID）由 HttpURLConnection 自己跟完。
   *  ⇒ 不打网页、不碰票的单次性，走的是我们自己的连接。 */
  async function casTicketExchange() {
    if (typeof Android === 'undefined' ||
        typeof Android.webCookieAll !== 'function') {
      return { ok: false, error: '当前 App 版本不支持读网页登录态，请先升级' };
    }
    // 这条路会用票换出一个**新的**教务会话，网页里那串原样转发必须撤掉 ——
    // 不然换完票紧接着 loadMainInfo 又会把旧 cookie 盖上去，白换一次。
    webLoginCookie = '';
    webLoginUA = '';

    var line = '';
    try { line = String(Android.webCookieAll('https://ssl.jxufe.edu.cn/') || ''); }
    catch (e) { line = ''; }
    if (!line) {
      return { ok: false,
               error: '网页里还没登录成功：用「喜鹊儿」扫码，看到教务首页就会自动返回' };
    }

    // 🔴 不按名字挑票（CASTGC / CASTGC_xxx / TGC 都是服务端实现细节，
    //    猜错就静默拿到空），整串倒进去 —— 多带几条用不上的 cookie 不会坏事。
    var jar = [];
    line.split(';').forEach(function (part) {
      var eq = part.indexOf('=');
      if (eq <= 0) return;
      var k = part.slice(0, eq).trim(), v = part.slice(eq + 1).trim();
      if (!k || !v) return;
      jar.push({ name: k, value: v, domain: 'ssl.jxufe.edu.cn', path: '/' });
    });
    if (!jar.length) {
      return { ok: false,
               error: '网页里还没登录成功：登录完、看到教务首页再回来' };
    }
    try { Android.eamsCookieSet(JSON.stringify(jar)); }
    catch (e2) { return { ok: false, error: '这个版本暂不支持网页登录，请更新后用' }; }

    var url = CAS_ROOT + '/cas/login?service=' + encodeURIComponent(CAS_SERVICE);
    var page = null;
    try {
      page = await req('GET', url, '', '', casHeaders());
    } catch (e3) {
      return { ok: false, error: '换票没成功，请检查网络后重试', kind: 'net' };
    }

    var landed = (page && page.url) || '';
    if (landed.indexOf('jwxt.jxufe.edu.cn') >= 0 &&
        landed.indexOf('/cas/login?service') < 0) {
      info.userCode = '';
      try { await loadMainInfo(); } catch (e4) { /* 下面统一判 userCode */ }
      if (info.userCode) {
        var saved = '';
        try { saved = String(Android.eamsCookieSave('') || ''); }
        catch (e5) { saved = 'fail: ' + e5; }
        return { ok: true, username: info.loginId, saved: saved };
      }
    }
    return { ok: false,
             error: '请在网页里重新登录一次，看到教务首页就会自动返回' };
  }

  /** 启动/同步时恢复已存 Cookie；没有或失效返回 null。 */
  async function restoreCookie() {
    // 从磁盘恢复的那份就是当前凭据，别让上一次网页登录留下的原样转发盖住它。
    webLoginCookie = '';
    webLoginUA = '';
    var raw = '';
    try { raw = String(Android.eamsCookieLoad() || ''); } catch (e) { return null; }
    if (!raw) return null;
    var jar = parseCookie(raw);
    try { Android.eamsCookieSet(JSON.stringify(jar)); } catch (e) { return null; }
    info.userCode = '';
    await loadMainInfo();
    return info.userCode ? { username: info.loginId } : null;
  }

  function clearCookie() {
    try { Android.eamsCookieClear(); } catch (e) { /* 老版本没有 */ }
    webLoginCookie = '';
    webLoginUA = '';
    info.userCode = '';
    semestersCache = [];
  }

  // ------------------------------------------------------------------ 对外 API
  window.JxufeApi = {
    isJxufe: true,
    sessionValid: sessionValid,
    loginWithPassword: loginWithPassword,
    loginWithCookie: loginWithCookie,
    loginWithWebCookie: loginWithWebCookie,
    restoreCookie: restoreCookie,
    clearCookie: clearCookie,
    sync: syncOnce,
    timetable: timetable,
    grades: grades,
    exams: exams,
    semesters: semesters,
    profile: profile,
    info: function () { return info; }
  };
})();
