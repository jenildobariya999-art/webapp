(function(){
"use strict";
function setHeaderStatus(status){
var badge=document.getElementById("headerBadge"),text=document.getElementById("headerStatusText");
if(!badge||!text)return;
badge.classList.remove("scanning","error","active","failed");
if(status==="SCANNING"){badge.classList.add("scanning");text.textContent="SCANNING";}
else if(status==="ERROR"){badge.classList.add("error");text.textContent="ERROR";}
else if(status==="ACTIVE"){badge.classList.add("active");text.textContent="ACTIVE";}
else if(status==="FAILED"){badge.classList.add("failed");text.textContent="FAILED";}
}
function showView(id){
document.querySelectorAll(".view-section").forEach(function(x){x.classList.remove("active");});
var v=document.getElementById(id);
if(v)v.classList.add("active");
}
function initializeTelegramUser(){
var user=null;
try{user=window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.initDataUnsafe&&window.Telegram.WebApp.initDataUnsafe.user;}catch(e){}
if(!user)return null;
var name=document.getElementById("userName"),uid=document.getElementById("userIdDisplay"),avatar=document.getElementById("avatarBox");
var fullName=((user.first_name||"")+" "+(user.last_name||"")).trim();
if(name)name.textContent=fullName||"Telegram User";
if(uid)uid.textContent=String(user.id||"");
if(avatar&&user.photo_url)avatar.style.backgroundImage="url('"+user.photo_url+"')";
return user;
}
function getParameters(){
var params=new URLSearchParams(window.location.search);
return{bot:params.get("bot"),botHash:params.get("botHash")};
}
function buildPayload(user,p,fp){
var timezone="unknown";
try{timezone=Intl.DateTimeFormat().resolvedOptions().timeZone||"unknown";}catch(e){}
return{
user_id:user?user.id:null,
bot:p.bot,
bot_hash:p.botHash,
visitorId:fp&&fp.visitorId?fp.visitorId:null,
device_id:navigator.userAgent,
user_agent:navigator.userAgent,
platform:navigator.platform,
language:navigator.language,
timezone:timezone,
hardware_concurrency:navigator.hardwareConcurrency||"unknown",
device_memory:navigator.deviceMemory||"unknown",
screen_resolution:screen.width+"x"+screen.height
};
}
function sendPayload(payload){
return fetch("https://verificationtest.byethost31.com/verificationFolder/process.php",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})
.then(function(response){
if(!response.ok)throw new Error("HTTP error: "+response.status);
return response.json();
})
.then(function(data){
console.log("PHP response:",data);
return data;
})
.catch(function(error){
console.error("Connection error:",error);
throw error;
});
}
function handleResponse(data){
if(!data){setHeaderStatus("ERROR");showView("view-failed");return;}
if(data.status==="success"){setHeaderStatus("ACTIVE");showView("view-success");return;}
if(data.status==="already"){setHeaderStatus("ACTIVE");showView("view-already");return;}
if(data.status==="failed"){
var msg=document.getElementById("failedMsg");
if(msg)msg.textContent=data.message||"Verification criteria not met.";
setHeaderStatus("FAILED");showView("view-failed");return;
}
setHeaderStatus("ERROR");showView("view-failed");
}
function startVerification(){
setHeaderStatus("SCANNING");
showView("view-scanning");
var user=initializeTelegramUser();
var params=getParameters();
if(!user){
setHeaderStatus("ERROR");
showView("view-failed");
return;
}
if(!params.botHash){
setHeaderStatus("ERROR");
showView("view-failed");
return;
}
if(typeof FingerprintJS==="undefined"){
var payload=buildPayload(user,params,null);
sendPayload(payload).then(handleResponse).catch(function(){setHeaderStatus("ERROR");showView("view-failed");});
return;
}
FingerprintJS.load().then(function(fp){return fp.get();}).then(function(result){
var payload=buildPayload(user,params,result);
return sendPayload(payload);
}).then(handleResponse).catch(function(error){
console.error(error);
setHeaderStatus("ERROR");
showView("view-failed");
});
}
document.addEventListener("DOMContentLoaded",function(){
startVerification();
});
})();
