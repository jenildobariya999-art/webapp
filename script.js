(function(){
"use strict";

var PROCESS_URL="https://verificationtest.byethost31.com/verificationFolder/process.php";

function setStatus(status){
var badge=document.getElementById("headerBadge");
var text=document.getElementById("headerStatusText");
if(badge)badge.classList.remove("scanning","error","active","failed");
if(status==="SCANNING"){
if(badge)badge.classList.add("scanning");
if(text)text.textContent="SCANNING";
}else if(status==="ERROR"){
if(badge)badge.classList.add("error");
if(text)text.textContent="ERROR";
}else if(status==="ACTIVE"){
if(badge)badge.classList.add("active");
if(text)text.textContent="ACTIVE";
}else if(status==="FAILED"){
if(badge)badge.classList.add("failed");
if(text)text.textContent="FAILED";
}
}

function showView(id){
var sections=document.querySelectorAll(".view-section");
for(var i=0;i<sections.length;i++)sections[i].classList.remove("active");
var view=document.getElementById(id);
if(view)view.classList.add("active");
}

function setMessage(message){
var el=document.getElementById("failedMsg");
if(el)el.textContent=message;
}

function getTelegramUser(){
try{
if(window.Telegram&&window.Telegram.WebApp){
if(window.Telegram.WebApp.ready)window.Telegram.WebApp.ready();
return window.Telegram.WebApp.initDataUnsafe&&window.Telegram.WebApp.initDataUnsafe.user?window.Telegram.WebApp.initDataUnsafe.user:null;
}
}catch(e){
console.error("Telegram error:",e);
}
return null;
}

function displayUser(user){
if(!user)return;
var name=document.getElementById("userName");
var uid=document.getElementById("userIdDisplay");
var avatar=document.getElementById("avatarBox");
var fullName=((user.first_name||"")+" "+(user.last_name||"")).trim();

if(name)name.textContent=fullName||"Telegram User";
if(uid)uid.textContent=String(user.id||"");
if(avatar&&user.photo_url)avatar.style.backgroundImage="url(\""+user.photo_url+"\")";
}

function getParams(){
var params=new URLSearchParams(window.location.search);
return{
bot:params.get("bot")||"",
bot_hash:params.get("botHash")||""
};
}

function getTimezone(){
try{
return Intl.DateTimeFormat().resolvedOptions().timeZone||"unknown";
}catch(e){
return"unknown";
}
}

function createPayload(user,params,fingerprint){
return{
user_id:user?user.id:null,
bot:params.bot,
bot_hash:params.bot_hash,
visitorId:fingerprint&&fingerprint.visitorId?fingerprint.visitorId:"",
device_id:navigator.userAgent||"",
user_agent:navigator.userAgent||"",
platform:navigator.platform||"",
language:navigator.language||"",
timezone:getTimezone(),
hardware_concurrency:navigator.hardwareConcurrency||0,
device_memory:navigator.deviceMemory||"unknown",
screen_resolution:(window.screen&&window.screen.width?window.screen.width:0)+"x"+(window.screen&&window.screen.height?window.screen.height:0)
};
}

function sendPayload(payload){
return fetch(PROCESS_URL,{
method:"POST",
headers:{
"Content-Type":"application/json",
"Accept":"application/json"
},
body:JSON.stringify(payload)
})
.then(function(response){
return response.text().then(function(text){
if(!response.ok)throw new Error("HTTP "+response.status+": "+text);
if(!text)throw new Error("Empty response from PHP");
var data;
try{
data=JSON.parse(text);
}catch(e){
throw new Error("Invalid JSON response: "+text);
}
return data;
});
});
}

function processResult(data){
if(!data){
setStatus("ERROR");
setMessage("Empty response from verification server.");
showView("view-failed");
return;
}

if(data.status==="success"||data.status==="active"){
setStatus("ACTIVE");
showView("view-success");
return;
}

if(data.status==="already"||data.status==="attempt"){
setStatus("ACTIVE");
showView("view-already");
return;
}

if(data.status==="failed"){
setStatus("FAILED");
setMessage(data.message||"Verification criteria not met.");
showView("view-failed");
return;
}

setStatus("ERROR");
setMessage(data.message||"Unknown verification response.");
showView("view-failed");
}

function getFingerprint(){
if(typeof FingerprintJS==="undefined")return Promise.resolve(null);

return FingerprintJS.load()
.then(function(fp){
return fp.get();
})
.catch(function(error){
console.error("FingerprintJS error:",error);
return null;
});
}

function verify(){
setStatus("SCANNING");
showView("view-scanning");

var params=getParams();
var user=getTelegramUser();

displayUser(user);

if(!user||!user.id){
setStatus("ERROR");
setMessage("Telegram user information could not be detected.");
showView("view-failed");
return;
}

if(!params.bot_hash){
setStatus("ERROR");
setMessage("Verification link is missing botHash.");
showView("view-failed");
return;
}

getFingerprint()
.then(function(fingerprint){
var payload=createPayload(user,params,fingerprint);
console.log("Verification payload:",payload);
return sendPayload(payload);
})
.then(function(data){
console.log("PHP response:",data);
processResult(data);
})
.catch(function(error){
console.error("Verification error:",error);
setStatus("ERROR");
setMessage("We encountered an error in verification. Please try again.");
showView("view-failed");
});
}

document.addEventListener("DOMContentLoaded",function(){
verify();
});

})();
