#pragma once
#include <string>
#include <vector>
#include <cstdint>
#include "color.h"
namespace ir {
struct Image { int w=0,h=0; std::vector<float> pixels; };
struct Session {
  std::string path, camera;
  Image preview;
  int width=0,height=0;
  float mr=1,mb=1,scale=1;
  double saturated=0;
  ColorContext color;
};
// EV, midtones, highlights, shadows, contrast, black, R, B,
// swap mode, swap %, saturation, hotspot, cool light, warm light,
// denoise, sharpen, swap highlight protection. G always equals one.
// Append: color scheme, separation, warm hue, cool hue, vibrance.
// Append: tone mapper, sigmoid hue preservation, DCP illuminant blend,
// DCP look amount, LUT amount, DCP enable, LUT linear/encoded input.
// Append warm and cool saturation. UI callers supply all 31 values; the
// default legacy look remains for native compatibility checks.
struct Params { float v[31]={0,0,0,0,0,0,1,1,0,0,100,0,0,0,0,0,0,1,60,85,85,35,0,100,50,0,100,0,0,0,0}; };
Image decode(const std::string& path,bool half,std::string* camera=nullptr,
             int* width=nullptr,int* height=nullptr,double* saturated=nullptr,int maxSide=1400,ColorContext* color=nullptr);
Session load(const std::string& path);
void neutral(Session& s,float x,float y);
void autoExposure(Session& s,float mr,float mb);
void render(const Image& src,const Params& p,float scale,uint8_t* dest,int stride,const ColorContext* color=nullptr);
void pixel(const float* rgb,const Params& p,float scale,float x,float y,float* out,const ColorContext* color=nullptr);
}
