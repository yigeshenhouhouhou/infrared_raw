#include "core.h"
#include <jni.h>
#include <android/bitmap.h>
#include <memory>
#include <stdexcept>
#include <cmath>
#include <cctype>
static void fail(JNIEnv* e,const char* message){e->ThrowNew(e->FindClass("java/lang/RuntimeException"),message);}
static ir::Session* get(jlong handle){if(!handle)throw std::runtime_error("Import a RAW image first.");return reinterpret_cast<ir::Session*>(handle);}
extern "C" JNIEXPORT jlong JNICALL Java_cn_infrared_studio_Native_open(JNIEnv* e,jclass,jstring path){
  try{const char* s=e->GetStringUTFChars(path,nullptr);std::string p(s);e->ReleaseStringUTFChars(path,s);
    return reinterpret_cast<jlong>(new ir::Session(ir::load(p)));}
  catch(const std::exception& ex){fail(e,ex.what());return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_cn_infrared_studio_Native_close(JNIEnv*,jclass,jlong h){delete reinterpret_cast<ir::Session*>(h);}
extern "C" JNIEXPORT jdoubleArray JNICALL Java_cn_infrared_studio_Native_info(JNIEnv* e,jclass,jlong h){
  try{auto& s=*get(h);double v[]={double(s.width),double(s.height),s.mr,s.mb,s.scale,s.saturated};
    auto a=e->NewDoubleArray(6);e->SetDoubleArrayRegion(a,0,6,v);return a;}
  catch(const std::exception& ex){fail(e,ex.what());return nullptr;}
}
extern "C" JNIEXPORT jstring JNICALL Java_cn_infrared_studio_Native_camera(JNIEnv* e,jclass,jlong h){
  try{return e->NewStringUTF(get(h)->camera.c_str());}catch(const std::exception& ex){fail(e,ex.what());return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_cn_infrared_studio_Native_neutral(JNIEnv* e,jclass,jlong h,jfloat x,jfloat y){
  try{ir::neutral(*get(h),x,y);}catch(const std::exception& ex){fail(e,ex.what());}
}
extern "C" JNIEXPORT void JNICALL Java_cn_infrared_studio_Native_autoExposure(JNIEnv* e,jclass,jlong h,jfloat r,jfloat b){
  try{ir::autoExposure(*get(h),r,b);}catch(const std::exception& ex){fail(e,ex.what());}
}
extern "C" JNIEXPORT void JNICALL Java_cn_infrared_studio_Native_setScale(JNIEnv* e,jclass,jlong h,jfloat scale){
  try{if(!std::isfinite(scale)||scale<=0)throw std::runtime_error("Invalid exposure scale.");get(h)->scale=scale;}
  catch(const std::exception& ex){fail(e,ex.what());}
}
extern "C" JNIEXPORT void JNICALL Java_cn_infrared_studio_Native_loadProfile(JNIEnv* e,jclass,jlong h,jstring path,jboolean lut){
  try{auto& s=*get(h);const char* chars=e->GetStringUTFChars(path,nullptr);std::string file(chars);e->ReleaseStringUTFChars(path,chars);
    if(file.empty()){if(lut)s.color.cube=ir::Cube();else{s.color.dcp=ir::Dcp();s.color.hasDcp=false;}return;}
    if(lut){auto next=ir::Cube::read(file);s.color.cube=std::move(next);}
    else{auto next=ir::Dcp::read(file);auto canonical=[](std::string x){std::string o;for(unsigned char c:x)if(std::isalnum(c))o+=char(std::tolower(c));return o;};
      auto model=canonical(next.model),camera=canonical(s.camera);
      if(!model.empty()&&model!=camera && camera.find(model)==std::string::npos && model.find(camera)==std::string::npos)throw std::runtime_error("DCP camera model does not match this RAW.");
      s.color.dcp=std::move(next);s.color.hasDcp=true;}
  }catch(const std::exception& ex){fail(e,ex.what());}
}
extern "C" JNIEXPORT jstring JNICALL Java_cn_infrared_studio_Native_profileInfo(JNIEnv* e,jclass,jlong h){
  try{const auto& s=*get(h);std::string info=s.color.hasCameraMatrix?"RAW camera matrix available":"No camera matrix; linear sRGB fallback";
    if(s.color.hasDcp)info+="\nDCP: "+s.color.dcp.name+(s.color.dcp.hasCM[1]||s.color.dcp.hasFM[1]?" [dual illuminant]":" [single illuminant]");
    if(s.color.cube.size)info+="\nLUT: "+s.color.cube.name+" "+std::to_string(s.color.cube.size)+"^3";
    return e->NewStringUTF(info.c_str());}catch(const std::exception& ex){fail(e,ex.what());return nullptr;}
}
extern "C" JNIEXPORT jobject JNICALL Java_cn_infrared_studio_Native_render(JNIEnv* e,jclass,jlong h,jdoubleArray values,jint full){
  try{
    auto& s=*get(h);ir::Params p;
    jsize count=e->GetArrayLength(values);
    if(count!=29)throw std::runtime_error("Render settings must contain 29 values, got "+std::to_string(count)+".");
    double v[29];e->GetDoubleArrayRegion(values,0,29,v);
    for(int i=0;i<29;i++){if(!std::isfinite(v[i]))throw std::runtime_error("Non-finite setting.");p.v[i]=float(v[i]);}
    ir::Image original;const ir::Image* image=&s.preview;
    if(full){original=ir::decode(s.path,full==2,nullptr,nullptr,nullptr,nullptr,0);image=&original;}
    jclass bitmap=e->FindClass("android/graphics/Bitmap");
    jclass config=e->FindClass("android/graphics/Bitmap$Config");
    jobject argb=e->GetStaticObjectField(config,e->GetStaticFieldID(config,"ARGB_8888","Landroid/graphics/Bitmap$Config;"));
    auto create=e->GetStaticMethodID(bitmap,"createBitmap","(IILandroid/graphics/Bitmap$Config;)Landroid/graphics/Bitmap;");
    jobject result=e->CallStaticObjectMethod(bitmap,create,image->w,image->h,argb);
    if(e->ExceptionCheck()||!result)return nullptr;
    AndroidBitmapInfo info;void* data=nullptr;
    if(AndroidBitmap_getInfo(e,result,&info)!=ANDROID_BITMAP_RESULT_SUCCESS ||
       AndroidBitmap_lockPixels(e,result,&data)!=ANDROID_BITMAP_RESULT_SUCCESS)
      throw std::runtime_error("Cannot allocate output bitmap. Try half-size export.");
    try{ir::render(*image,p,s.scale,static_cast<uint8_t*>(data),info.stride,&s.color);}
    catch(...){AndroidBitmap_unlockPixels(e,result);throw;}
    AndroidBitmap_unlockPixels(e,result);return result;
  }catch(const std::bad_alloc&){fail(e,"Not enough memory. Try half-size export.");return nullptr;}
  catch(const std::exception& ex){fail(e,ex.what());return nullptr;}
}
