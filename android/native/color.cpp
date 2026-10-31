// Independent implementation from published matrix / TIFF / LUT definitions.
// This product includes DNG technology under license by Adobe.
#include "color.h"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <cstring>
#include <map>
#include <cstdint>
namespace ir {
static float clip(float x,float a=0,float b=1){return std::max(a,std::min(x,b));}
const Matrix XYZ50_TO_PROPHOTO={1.3459433f,-.2556075f,-.0511118f,-.5445989f,1.5081673f,.0205351f,0,0,1.2118128f};
const Matrix D65_TO_D50={1.0478112f,.0228866f,-.0501270f,.0295424f,.9904844f,-.0170491f,-.0092345f,.0150436f,.7521316f};
static const Matrix PROPHOTO_TO_XYZ50={.7976749f,.1351917f,.0313534f,.2880402f,.7118741f,.0000857f,0,0,.8252100f};
static const Matrix XYZ65_TO_SRGB={3.2404542f,-1.5371385f,-.4985314f,-.9692660f,1.8760108f,.0415560f,.0556434f,-.2040259f,1.0572252f};
Matrix multiply(const Matrix& a,const Matrix& b){Matrix o{};for(int y=0;y<3;y++)for(int x=0;x<3;x++)for(int k=0;k<3;k++)o[y*3+x]+=a[y*3+k]*b[k*3+x];return o;}
Matrix inverse(const Matrix& a){
  float d=a[0]*(a[4]*a[8]-a[5]*a[7])-a[1]*(a[3]*a[8]-a[5]*a[6])+a[2]*(a[3]*a[7]-a[4]*a[6]);
  if(!std::isfinite(d)||std::fabs(d)<1e-9)throw std::runtime_error("Singular camera/profile matrix.");
  Matrix o={a[4]*a[8]-a[5]*a[7],a[2]*a[7]-a[1]*a[8],a[1]*a[5]-a[2]*a[4],a[5]*a[6]-a[3]*a[8],a[0]*a[8]-a[2]*a[6],a[2]*a[3]-a[0]*a[5],a[3]*a[7]-a[4]*a[6],a[1]*a[6]-a[0]*a[7],a[0]*a[4]-a[1]*a[3]};
  for(float& x:o)x/=d;return o;
}
void transform(const Matrix& m,const float* a,float* b){float t[3];for(int r=0;r<3;r++)t[r]=m[r*3]*a[0]+m[r*3+1]*a[1]+m[r*3+2]*a[2];std::copy(t,t+3,b);}
Matrix cameraMatrix(const Matrix& cm,const float* white){
  float response[3];transform(cm,white,response);Matrix inv=inverse(cm);
  for(int r=0;r<3;r++)for(int c=0;c<3;c++)inv[r*3+c]*=response[c];
  // A white-balanced camera triplet (1,1,1) maps to this reference XYZ white.
  return inv;
}
const Matrix PROPHOTO_TO_SRGB=multiply(multiply(XYZ65_TO_SRGB,inverse(D65_TO_D50)),PROPHOTO_TO_XYZ50);
const Matrix SRGB_TO_PROPHOTO=inverse(PROPHOTO_TO_SRGB);
static void hsv(const float* a,float& h,float& s,float& v){
  v=std::max({a[0],a[1],a[2]});float mn=std::min({a[0],a[1],a[2]}),d=v-mn;h=0;s=v>1e-12?d/v:0;
  if(d>1e-12){if(v==a[0])h=std::fmod((a[1]-a[2])/d+6,6)*60;else if(v==a[1])h=((a[2]-a[0])/d+2)*60;else h=((a[0]-a[1])/d+4)*60;}
}
static void rgb(float h,float s,float v,float* out){
  h=std::fmod(h+720,360);float c=v*s,x=c*(1-std::fabs(std::fmod(h/60,2)-1)),m=v-c;
  float t[6][3]={{c,x,0},{x,c,0},{0,c,x},{0,x,c},{x,0,c},{c,0,x}};int k=std::min(5,int(h/60));for(int i=0;i<3;i++)out[i]=t[k][i]+m;
}
static float enc(float x){return x<=.0031308f?12.92f*x:1.055f*std::pow(x,1/2.4f)-.055f;}
void compressGamut(float* a){
  // Compress the chroma vector about a non-negative luminance, not each channel.
  float y=std::max(0.f,.2126f*a[0]+.7152f*a[1]+.0722f*a[2]),t=1;
  for(int c=0;c<3;c++)if(a[c]<0)t=std::min(t,y/std::max(y-a[c],1e-12f));
  for(int c=0;c<3;c++)a[c]=std::max(0.f,y+t*(a[c]-y));
}
void profiledColor(float* a,int mode,const ColorContext& ctx,float blend,bool useDcp){
  if(mode<2)return;
  if(mode==6){std::swap(a[0],a[2]);return;} // darktable-style linear infrared BGR input.
  Matrix input=ctx.prepared?ctx.activeInput:(useDcp && ctx.hasDcp?ctx.dcp.inputMatrix(blend):ctx.cameraToProPhoto);
  transform(input,a,a);
  if(useDcp && ctx.hasDcp){
    // Base map is camera characterization, look table is an optional later look.
    if(ctx.dcp.base[0].h && ctx.dcp.base[1].h){float x[3]={a[0],a[1],a[2]},y[3]={a[0],a[1],a[2]};ctx.dcp.base[0].apply(x);ctx.dcp.base[1].apply(y);for(int c=0;c<3;c++)a[c]=(1-blend)*x[c]+blend*y[c];}
    else if(ctx.dcp.base[0].h)ctx.dcp.base[0].apply(a);
    else if(ctx.dcp.base[1].h)ctx.dcp.base[1].apply(a);
  }
  // anxix/rt_ir_presets, CC-BY-SA-4.0. These are working-ProPhoto matrices,
  // not complete RawTherapee PP3 rendering equivalence.
  static const Matrix mixers[3]={
    {-.11f,.05f,1.f,-.40f,.83f,.46f,.97f,.24f,-.46f},
    {-.110f,.050f,1.f,-.400f,.830f,.460f,1.233f,.209f,-.480f},
    {-.09f,.13f,1.14f,-.13f,.80f,.25f,1.06f,.01f,-.13f}};
  if(mode>=3 && mode<=5){
    // The published mixer rows do not sum to one. Normalize each row so a
    // picked neutral stays neutral with our different input transform.
    Matrix m=mixers[mode-3];
    for(int r=0;r<3;r++){float sum=m[3*r]+m[3*r+1]+m[3*r+2];for(int c=0;c<3;c++)m[3*r+c]/=sum;}
    transform(m,a,a);
  }
  transform(PROPHOTO_TO_SRGB,a,a);compressGamut(a);
}
float sigmoid(float x,float contrast){
  // Generalised log-logistic, black=0, white=1, fixed scene 18% grey pivot.
  // Independent implementation; not a binary-compatible copy of darktable.
  if(x<=0)return 0;
  float logit=contrast*std::log(x/.18f)+std::log(.18f/.82f);
  return logit>=0?1/(1+std::exp(-logit)):std::exp(logit)/(1+std::exp(logit));
}
void viewTransform(float* a,int mode,float contrast,float hueProtect,float highlights){
  float peak=std::max({a[0],a[1],a[2]});if(peak<=0){a[0]=a[1]=a[2]=0;return;}
  if(mode==0){float knee=.8f-.5f*highlights/100,v=peak;if(v>knee){float c=std::log1p((v-knee)/(1-knee));v=knee+(1-knee)*c/(1+c);}for(int i=0;i<3;i++)a[i]*=v/peak;return;}
  float c=std::max(.45f,1.4f+.55f*contrast/100-.25f*highlights/100),top=sigmoid(peak,c);
  for(int i=0;i<3;i++){float ratio=a[i]/peak*top,channel=sigmoid(a[i],c);a[i]=mode==1?ratio:(1-hueProtect)*channel+hueProtect*ratio;}
}
void HsvTable::apply(float* a,float strength)const{
  if(!h||data.empty()||strength<=0)return;
  float hue,sat,val;hsv(a,hue,sat,val);if(sat<0||val<0)return;
  float coords[3]={hue/360*h,clip(sat)*(s-1),clip(encoding?enc(val):val)*(v-1)};
  int base[3]={int(std::floor(coords[0]))%h,int(std::floor(coords[1])),int(std::floor(coords[2]))};
  float f[3]={coords[0]-std::floor(coords[0]),coords[1]-base[1],coords[2]-base[2]},o[3]={0,0,0};
  for(int iv=0;iv<2;iv++)for(int ih=0;ih<2;ih++)for(int is=0;is<2;is++){
    int vv=std::min(v-1,base[2]+iv),hh=(base[0]+ih)%h,ss=std::min(s-1,base[1]+is);
    float w=(iv?f[2]:1-f[2])*(ih?f[0]:1-f[0])*(is?f[1]:1-f[1]);
    size_t k=((size_t(vv)*h+hh)*s+ss)*3;for(int c=0;c<3;c++)o[c]+=w*data[k+c];
  }
  float out[3];rgb(hue+o[0],clip(sat*o[1]),std::max(0.f,val*o[2]),out);
  for(int c=0;c<3;c++)a[c]+=(out[c]-a[c])*clip(strength);
}
void applyDcpCurve(float* a,const std::vector<float>& curve){
  if(curve.size()<4)return;float peak=std::max({a[0],a[1],a[2]});if(peak<=0)return;
  float x=clip(peak),y=curve.back();for(size_t i=2;i<curve.size();i+=2)if(x<=curve[i]){float t=(x-curve[i-2])/std::max(1e-12f,curve[i]-curve[i-2]);y=curve[i-1]+t*(curve[i+1]-curve[i-1]);break;}
  for(int c=0;c<3;c++)a[c]*=y/peak;
}
struct Tiff {
  std::vector<uint8_t> b;bool little=true;
  struct Entry {unsigned type;uint32_t count;size_t at;};std::map<int,Entry> entries;
  void bounds(size_t at,size_t n)const{if(at>b.size()||n>b.size()-at)throw std::runtime_error("Truncated or invalid DCP offsets.");}
  uint16_t u16(size_t at)const{bounds(at,2);return little?b[at]|b[at+1]<<8:b[at]<<8|b[at+1];}
  uint32_t u32(size_t at)const{bounds(at,4);uint32_t v=0;for(int i=0;i<4;i++)v|=uint32_t(b[at+i])<<(little?8*i:8*(3-i));return v;}
  explicit Tiff(const std::string& path){
    std::ifstream f(path,std::ios::binary|std::ios::ate);if(!f)throw std::runtime_error("Cannot read DCP.");auto n=f.tellg();if(n<8||n>32*1024*1024)throw std::runtime_error("DCP must be 8 bytes to 32 MB.");b.resize(size_t(n));f.seekg(0);f.read(reinterpret_cast<char*>(b.data()),b.size());
    little=b[0]=='I'&&b[1]=='I';if(!little&&!(b[0]=='M'&&b[1]=='M'))throw std::runtime_error("Not a TIFF/DCP profile.");
    auto magic=u16(2);if(magic!=42&&magic!=0x4352&&magic!=0x5243)throw std::runtime_error("Unsupported DCP header.");
    size_t at=u32(4);uint16_t count=u16(at);if(count>1024)throw std::runtime_error("Too many DCP tags.");bounds(at+2,size_t(count)*12);
    for(unsigned i=0;i<count;i++){size_t e=at+2+12*i;unsigned tag=u16(e),type=u16(e+2);uint32_t cnt=u32(e+4);static int sizes[]={0,1,1,2,4,8,1,1,2,4,8,4,8};
      if(type>12||!sizes[type])continue;if(cnt>4000000)throw std::runtime_error("DCP tag too large.");size_t bytes=size_t(cnt)*sizes[type],offset=bytes<=4?e+8:u32(e+8);bounds(offset,bytes);entries[tag]={type,cnt,offset};}
  }
  std::vector<float> values(int tag)const{
    auto found=entries.find(tag);if(found==entries.end())return {};auto e=found->second;std::vector<float> o;o.reserve(e.count);
    for(uint32_t i=0;i<e.count;i++){float x=0;switch(e.type){case 1:x=b[e.at+i];break;case 3:x=u16(e.at+i*2);break;case 4:x=float(u32(e.at+i*4));break;
      case 5:case 10:{int64_t num=e.type==10?int32_t(u32(e.at+i*8)):int64_t(u32(e.at+i*8));int64_t den=e.type==10?int32_t(u32(e.at+i*8+4)):int64_t(u32(e.at+i*8+4));if(!den)throw std::runtime_error("Zero DCP rational denominator.");x=float(double(num)/den);break;}
      case 11:{uint32_t v=u32(e.at+i*4);std::memcpy(&x,&v,4);break;}default:throw std::runtime_error("Unsupported DCP numeric tag type.");}
      if(!std::isfinite(x))throw std::runtime_error("Non-finite DCP value.");o.push_back(x);}
    return o;
  }
  std::string text(int tag)const{auto it=entries.find(tag);if(it==entries.end())return "";auto e=it->second;if((e.type!=2&&e.type!=1)||e.count>4096)throw std::runtime_error("Invalid DCP text.");std::string s(reinterpret_cast<const char*>(&b[e.at]),e.count);auto z=s.find('\0');if(z!=std::string::npos)s.resize(z);return s;}
};
Dcp Dcp::read(const std::string& path){
  Tiff t(path);Dcp d;d.name=t.text(50936);d.model=t.text(50708);
  for(int i=0;i<2;i++){auto cm=t.values(50721+i),fm=t.values(50964+i);if(!cm.empty()){if(cm.size()!=9)throw std::runtime_error("DCP requires RGB 3x3 matrices.");std::copy(cm.begin(),cm.end(),d.cm[i].begin());inverse(d.cm[i]);d.hasCM[i]=true;}if(!fm.empty()){if(fm.size()!=9)throw std::runtime_error("Invalid DCP forward matrix.");std::copy(fm.begin(),fm.end(),d.fm[i].begin());inverse(d.fm[i]);d.hasFM[i]=true;}}
  if(!d.hasCM[0]&&!d.hasCM[1]&&!d.hasFM[0]&&!d.hasFM[1])throw std::runtime_error("DCP contains no supported RGB matrix.");
  auto table=[&](int dimsTag,int dataTag,int encodingTag){HsvTable a;auto dims=t.values(dimsTag),data=t.values(dataTag);if(dims.empty()&&data.empty())return a;if(dims.size()!=3)throw std::runtime_error("Invalid DCP table dimensions.");a.h=int(dims[0]);a.s=int(dims[1]);a.v=int(dims[2]);if(a.h<1||a.s<2||a.v<1||a.h>360||a.s>256||a.v>256||size_t(a.h)*a.s*a.v*3!=data.size())throw std::runtime_error("Invalid DCP HSV table.");auto enc=t.values(encodingTag);a.encoding=enc.empty()?0:int(enc[0]);if(a.encoding<0||a.encoding>1)throw std::runtime_error("Unsupported DCP HSV encoding.");a.data.swap(data);return a;};
  d.base[0]=table(50937,50938,51107);if(t.entries.count(50939))d.base[1]=table(50937,50939,51107);
  d.look=table(50981,50982,51108);d.curve=t.values(50940);if(d.curve.size()%2)throw std::runtime_error("Invalid DCP tone curve.");
  for(size_t i=0;i<d.curve.size();i+=2){if(d.curve[i]<0||d.curve[i]>1||d.curve[i+1]<0||d.curve[i+1]>1||(i&&(d.curve[i]<=d.curve[i-2]||d.curve[i+1]<d.curve[i-1])))throw std::runtime_error("DCP tone curve must be monotonic in [0,1].");}
  if(!d.curve.empty() && (d.curve.front()!=0||d.curve[1]!=0||d.curve[d.curve.size()-2]!=1||d.curve.back()!=1))throw std::runtime_error("Only SDR DCP tone curves with endpoints (0,0),(1,1) are supported.");
  auto offset=t.values(51109);if(!offset.empty()){d.baseline=offset[0];if(std::fabs(d.baseline)>16)throw std::runtime_error("Invalid DCP baseline exposure.");}
  // Unimplemented rendering extensions are rejected instead of silently ignored.
  for(int tag:{52525,52526,52531,52532})if(t.entries.count(tag))throw std::runtime_error("This DCP uses unsupported newer rendering extensions.");
  return d;
}
Matrix Dcp::inputMatrix(float blend,const float* neutral)const{
  blend=clip(blend);Matrix out{};
  auto combine=[&](const Matrix* m,const bool* has){if(has[0]&&has[1]){for(int i=0;i<9;i++)out[i]=m[0][i]*(1-blend)+m[1][i]*blend;}else out=m[has[0]?0:1];};
  if(hasFM[0]||hasFM[1])combine(fm,hasFM);
  else{combine(cm,hasCM);const float d50[]={.96422f,1,.82521f};
    if(neutral){
      Matrix camToXYZ=inverse(out);float white[3];transform(camToXYZ,neutral,white);float y=white[1];
      if(y<=1e-9||white[0]<=0||white[2]<=0)throw std::runtime_error("DCP matrix is incompatible with this white balance.");
      for(float& v:white)v/=y;
      const Matrix bradford={.8951f,.2664f,-.1614f,-.7502f,1.7135f,.0367f,.0389f,-.0685f,1.0296f};
      float src[3],dst[3];transform(bradford,white,src);transform(bradford,d50,dst);Matrix scaled=bradford;
      for(int r=0;r<3;r++){if(src[r]<=1e-9)throw std::runtime_error("Invalid DCP adaptation white.");for(int c=0;c<3;c++)scaled[r*3+c]*=dst[r]/src[r];}
      Matrix adapt=multiply(inverse(bradford),scaled);
      for(int r=0;r<3;r++)for(int c=0;c<3;c++)camToXYZ[r*3+c]*=neutral[c]/y;
      out=multiply(adapt,camToXYZ);
    }else out=cameraMatrix(out,d50);
  }
  return multiply(XYZ50_TO_PROPHOTO,out);
}
Cube Cube::read(const std::string& path){
  std::ifstream f(path);if(!f)throw std::runtime_error("Cannot open .cube LUT.");Cube a;std::string line;size_t lines=0;
  while(std::getline(f,line)){if(++lines>300000||line.size()>4096)throw std::runtime_error("LUT too large.");auto hash=line.find('#');if(hash!=std::string::npos)line.resize(hash);std::istringstream s(line);std::string token;if(!(s>>token))continue;
    if(token=="TITLE"){std::getline(s,a.name);continue;}
    if(token=="LUT_1D_SIZE")throw std::runtime_error("Only 3D .cube LUTs are supported.");
    if(token=="LUT_3D_SIZE"){if(a.size||!(s>>a.size)||a.size<2||a.size>65)throw std::runtime_error("LUT size must be 2 to 65.");continue;}
    if(token=="DOMAIN_MIN"||token=="DOMAIN_MAX"){float* x=token=="DOMAIN_MIN"?a.lo:a.hi;for(int i=0;i<3;i++)if(!(s>>x[i])||!std::isfinite(x[i]))throw std::runtime_error("Invalid LUT domain.");continue;}
    if(!a.size)throw std::runtime_error("LUT_3D_SIZE missing.");
    std::istringstream row(line);for(int i=0;i<3;i++){float v;if(!(row>>v)||!std::isfinite(v)||std::fabs(v)>16)throw std::runtime_error("Invalid LUT colour value.");a.data.push_back(v);}std::string extra;if(row>>extra)throw std::runtime_error("Unexpected LUT row data.");
  }
  if(a.size==0||a.data.size()!=size_t(a.size)*a.size*a.size*3)throw std::runtime_error("LUT sample count does not match LUT_3D_SIZE.");for(int i=0;i<3;i++)if(a.hi[i]<=a.lo[i])throw std::runtime_error("Invalid LUT domain range.");return a;
}
void Cube::apply(float* a,float strength)const{
  if(!size||strength<=0)return;int base[3],order[3]={0,1,2};float f[3];
  for(int c=0;c<3;c++){float x=clip((a[c]-lo[c])/(hi[c]-lo[c]))*(size-1);base[c]=std::min(size-2,int(x));f[c]=x-base[c];}
  std::sort(order,order+3,[&](int x,int y){return f[x]>f[y];});
  auto sample=[&](const int* p,int c){return data[((size_t(p[2])*size+p[1])*size+p[0])*3+c];};
  int p1[3]={base[0],base[1],base[2]},p2[3]={base[0],base[1],base[2]},p3[3]={base[0]+1,base[1]+1,base[2]+1};p1[order[0]]++;p2[order[0]]++;p2[order[1]]++;
  for(int c=0;c<3;c++){float c0=sample(base,c),c1=sample(p1,c),c2=sample(p2,c),c3=sample(p3,c);float o=c0+f[order[0]]*(c1-c0)+f[order[1]]*(c2-c1)+f[order[2]]*(c3-c2);a[c]+=(o-a[c])*clip(strength);}
}
}
