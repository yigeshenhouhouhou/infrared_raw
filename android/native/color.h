#pragma once
#include <string>
#include <vector>
#include <array>
namespace ir {
using Matrix=std::array<float,9>;
Matrix multiply(const Matrix& a,const Matrix& b);
Matrix inverse(const Matrix& a);
void transform(const Matrix& m,const float* a,float* b);
Matrix cameraMatrix(const Matrix& xyzToCamera,const float* white);
extern const Matrix XYZ50_TO_PROPHOTO,PROPHOTO_TO_SRGB,SRGB_TO_PROPHOTO,D65_TO_D50;
struct HsvTable { int h=0,s=0,v=0,encoding=0;std::vector<float> data;void apply(float* rgb,float strength=1)const; };
struct Dcp {
  std::string name,model;
  Matrix cm[2],fm[2];bool hasCM[2]={false,false},hasFM[2]={false,false};
  HsvTable base[2],look;std::vector<float> curve;
  float baseline=0;
  Matrix inputMatrix(float blend,const float* neutral=nullptr)const;
  static Dcp read(const std::string& path);
};
struct Cube {
  int size=0;std::string name;float lo[3]={0,0,0},hi[3]={1,1,1};std::vector<float> data;
  void apply(float* rgb,float strength)const;
  static Cube read(const std::string& path);
};
struct ColorContext {
  Matrix cameraToProPhoto=SRGB_TO_PROPHOTO;bool hasCameraMatrix=false;
  Matrix activeInput=SRGB_TO_PROPHOTO;bool prepared=false;
  Dcp dcp;bool hasDcp=false;Cube cube;
};
void compressGamut(float* rgb);
void profiledColor(float* rgb,int mode,const ColorContext& ctx,float dcpBlend,bool useDcp);
float sigmoid(float x,float contrast);
void viewTransform(float* rgb,int mode,float contrast,float hueProtect,float highlights);
void applyDcpCurve(float* rgb,const std::vector<float>& curve);
}
