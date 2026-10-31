#include "core.h"
#include "libraw/libraw.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <memory>
#include <cstring>
namespace ir {
static float clamp(float x,float lo=0,float hi=1){return std::max(lo,std::min(x,hi));}
static void check(int err){if(err!=LIBRAW_SUCCESS)throw std::runtime_error(LibRaw::strerror(err));}
Image decode(const std::string& path,bool half,std::string* camera,int* width,int* height,double* saturated,int maxSide,ColorContext* color){
  LibRaw raw;
  raw.imgdata.rawparams.max_raw_memory_mb=1024;
  check(raw.open_file(path.c_str()));
  auto& d=raw.imgdata;
  if(d.idata.colors!=3 || std::string(d.idata.cdesc)!="RGBG")
    throw std::runtime_error("This version supports three-colour RGB Bayer / X-Trans RAW only.");
  if(uint64_t(d.sizes.raw_width)*d.sizes.raw_height>100000000)
    throw std::runtime_error("RAW exceeds the mobile decoder's 100 MP limit.");
  if(!half && uint64_t(d.sizes.width)*d.sizes.height>36000000)
    throw std::runtime_error("Above 36 MP: select half-size JPG export to reduce memory usage.");
  if(camera)*camera=std::string(d.idata.make)+" "+d.idata.model;
  if(width)*width=d.sizes.width;
  if(height)*height=d.sizes.height;
  if(color){
    try{Matrix cm{};for(int r=0;r<3;r++)for(int c=0;c<3;c++)cm[r*3+c]=d.color.cam_xyz[r][c];
      const float white[]={.95047f,1,1.08883f};
      color->cameraToProPhoto=multiply(multiply(XYZ50_TO_PROPHOTO,D65_TO_D50),cameraMatrix(cm,white));color->hasCameraMatrix=true;
    }catch(const std::exception&){color->cameraToProPhoto=SRGB_TO_PROPHOTO;color->hasCameraMatrix=false;}
  }
  check(raw.unpack());
  // Record clipping before development. Inspect a bounded grid of CFA samples.
  if(saturated){
    uint64_t count=0,clipped=0;
    if(d.rawdata.raw_image){
      unsigned pitch=d.sizes.raw_pitch/sizeof(ushort);
      for(unsigned y=0;y<d.sizes.height;y+=5)for(unsigned x=0;x<d.sizes.width;x+=7){
        unsigned ry=y+d.sizes.top_margin,rx=x+d.sizes.left_margin;
        int c=raw.COLOR(y,x);
        float white=d.color.linear_max[c]>0?d.color.linear_max[c]:d.color.maximum;
        if(d.rawdata.raw_image[ry*pitch+rx]>=white)++clipped;
        ++count;
      }
    }
    *saturated=count?100.0*clipped/count:0;
  }
  float black[4],white[4];
  for(int c=0;c<4;c++){
    black[c]=d.color.black+d.color.cblack[c];
    white[c]=d.color.linear_max[c]>black[c]?d.color.linear_max[c]:d.color.maximum;
  }
  float reference=std::max(1.f,((white[1]-black[1])+(white[3]-black[3]))*.5f);
  auto& p=d.params;
  p.half_size=half?1:0; p.use_camera_wb=0;p.use_auto_wb=0;
  for(int c=0;c<4;c++)p.user_mul[c]=1;
  p.no_auto_scale=1;p.no_auto_bright=1;p.adjust_maximum_thr=0;
  p.output_color=0;p.output_bps=16;p.gamm[0]=1;p.gamm[1]=1;
  p.highlight=1;p.user_qual=half?0:3;
  check(raw.dcraw_process());
  int error=0;
  auto* image=raw.dcraw_make_mem_image(&error);check(error);
  if(!image)throw std::runtime_error("No decoded RAW image.");
  std::unique_ptr<libraw_processed_image_t,void(*)(libraw_processed_image_t*)>
      owned(image,LibRaw::dcraw_clear_mem);
  if(image->bits!=16 || image->colors!=3)throw std::runtime_error("Unsupported decoded pixel layout.");
  // Copy only the downsampled preview, then release all LibRaw allocations.
  int factor=half && maxSide>0?std::max(1,int(std::ceil(std::max(image->width,image->height)/double(maxSide)))):1;
  Image result;result.w=(image->width+factor-1)/factor;result.h=(image->height+factor-1)/factor;
  result.pixels.resize(size_t(result.w)*result.h*3);
  const uint16_t* source=reinterpret_cast<const uint16_t*>(image->data);
  for(int y=0;y<result.h;y++)for(int x=0;x<result.w;x++){
    float sum[3]={0,0,0};int n=0;
    for(int by=y*factor;by<std::min(int(image->height),(y+1)*factor);by++)
      for(int bx=x*factor;bx<std::min(int(image->width),(x+1)*factor);bx++){
        size_t k=(size_t(by)*image->width+bx)*3;
        for(int c=0;c<3;c++)sum[c]+=source[k+c];++n;
      }
    for(int c=0;c<3;c++)result.pixels[(size_t(y)*result.w+x)*3+c]=sum[c]/(n*reference);
  }
  return result;
}
static float mean(std::vector<float> a){
  if(a.size()<8)throw std::runtime_error("Neutral sample is too dark, clipped or small.");
  for(int t=0;t<3;t++){
    double m=0,s=0;for(float x:a)m+=x;m/=a.size();
    for(float x:a)s+=(x-m)*(x-m);s=std::sqrt(s/a.size());
    if(s<1e-10)return float(m);
    std::vector<float> next;for(float x:a)if(std::abs(x-m)<2.5*s)next.push_back(x);
    if(next.size()<8||next.size()==a.size())return float(m);a.swap(next);
  }
  double sum=0;for(float x:a)sum+=x;return float(sum/a.size());
}
void autoExposure(Session& s,float mr,float mb){
  std::vector<float> peaks;peaks.reserve(s.preview.w*s.preview.h/4+1);
  std::vector<float> lum;lum.reserve(peaks.capacity());
  for(int y=0;y<s.preview.h;y+=2)for(int x=0;x<s.preview.w;x+=2){
    const float* a=&s.preview.pixels[(size_t(y)*s.preview.w+x)*3];
    peaks.push_back(std::max({a[0]*mr,a[1],a[2]*mb}));
    float y=.2126f*a[0]*mr+.7152f*a[1]+.0722f*a[2]*mb;
    if(std::isfinite(y) && y>1e-6f)lum.push_back(y);
  }
  auto percentile=[](std::vector<float>& a,double q){
    double index=q*(a.size()-1);size_t lo=size_t(index),hi=std::min(a.size()-1,lo+1);
    std::nth_element(a.begin(),a.begin()+lo,a.end());double low=a[lo];
    std::nth_element(a.begin(),a.begin()+hi,a.end());return float(low+(a[hi]-low)*(index-lo));
  };
  if(lum.empty()){s.scale=1;return;}
  float key=.4f*percentile(lum,.50)+.6f*percentile(lum,.75);
  float floor=.65f/std::max(percentile(peaks,.995),1e-6f);
  s.scale=clamp(std::max(floor,.28f/std::max(key,1e-6f)),1.f/256,256);
}
static void solve(Session& s,int x0,int y0,int x1,int y1){
  std::vector<float> samples[3];
  int step=std::max(1,int(std::sqrt(double((x1-x0)*(y1-y0))/50000)));
  for(int y=y0;y<y1;y+=step)for(int x=x0;x<x1;x+=step){
    const float* a=&s.preview.pixels[(size_t(y)*s.preview.w+x)*3];
    for(int c=0;c<3;c++)if(a[c]>0 && a[c]<.98)samples[c].push_back(a[c]);
  }
  float r=mean(samples[0]),g=mean(samples[1]),b=mean(samples[2]);
  if(std::min({r,g,b})<=1e-8)throw std::runtime_error("No usable neutral signal.");
  s.mr=clamp(g/r,.0001,10000);s.mb=clamp(g/b,.0001,10000);
}
Session load(const std::string& path){
  Session s;s.path=path;s.preview=decode(path,true,&s.camera,&s.width,&s.height,&s.saturated,1400,&s.color);
  solve(s,0,0,s.preview.w,s.preview.h);autoExposure(s,s.mr,s.mb);return s;
}
void neutral(Session& s,float x,float y){
  int w=s.preview.w,h=s.preview.h,cx=int(clamp(x)*std::max(0,w-1)),cy=int(clamp(y)*std::max(0,h-1));
  int radius=std::max(4,int(std::min(w,h)*.02));
  solve(s,std::max(0,cx-radius),std::max(0,cy-radius),std::min(w,cx+radius+1),std::min(h,cy+radius+1));
}
static float encode(float x){return x<=.0031308f?12.92f*x:1.055f*std::pow(x,1.f/2.4f)-.055f;}
static float hueWeight(float h,float center,float width){float d=std::fabs(std::fmod(h-center+540,360)-180);return clamp(1-d/width);}
static float smooth(float v){v=clamp(v);return v*v*(3-2*v);}
static void toHSV(const float* a,float& h,float& s,float& v){
  v=std::max({a[0],a[1],a[2]});float mn=std::min({a[0],a[1],a[2]}),d=v-mn;
  h=0;s=v>1e-8f?d/v:0;
  if(d>1e-8f){if(v==a[0])h=std::fmod((a[1]-a[2])/d+6,6)*60;
    else if(v==a[1])h=((a[2]-a[0])/d+2)*60;
    else h=((a[0]-a[1])/d+4)*60;}
}
static void fromHSV(float h,float s,float v,float* a){
  h=std::fmod(h+360,360);float c=v*s,z=c*(1-std::fabs(std::fmod(h/60,2)-1)),m=v-c;
  float values[6][3]={{c,z,0},{z,c,0},{0,c,z},{0,z,c},{z,0,c},{c,0,z}};
  int sector=std::max(0,std::min(5,int(h/60)));for(int i=0;i<3;i++)a[i]=values[sector][i]+m;
}
void pixel(const float* input,const Params& p,float scale,float x,float y,float* a,const ColorContext* context){
  static const ColorContext fallback{};
  const ColorContext& ctx=context?*context:fallback;
  float gain=scale*std::exp2(p.v[0]);
  if(p.v[27]>.5f && ctx.hasDcp)gain*=std::exp2(ctx.dcp.baseline);
  float rgb[3]={std::max(0.f,input[0]*p.v[6]),std::max(0.f,input[1]),std::max(0.f,input[2]*p.v[7])};
  int profile=int(p.v[17]);
  if(profile>=2)profiledColor(rgb,profile,ctx,p.v[24]/100,p.v[27]>.5f);
  for(int c=0;c<3;c++)rgb[c]*=gain;
  if(profile==1){
    float oldpeak=std::max({rgb[0],rgb[1],rgb[2]}),oldmin=std::min({rgb[0],rgb[1],rgb[2]});
    if(oldpeak-oldmin>1e-6f*std::max(oldpeak,1e-12f)){
      float r=rgb[0],g=rgb[1],b=rgb[2],lum=.2126f*r+.7152f*g+.0722f*b;
      float mixed[3]={.05f*r+.15f*g+.80f*b,.05f*r+.90f*g+.05f*b,.85f*r+.10f*g+.05f*b};
      float peak=std::max({mixed[0],mixed[1],mixed[2]});
      float chroma=(peak-std::min({mixed[0],mixed[1],mixed[2]}))/std::max(peak,1e-12f);
      float exponent=1+.025f*p.v[18]*chroma/(chroma+.02f)*clamp(peak/.015f);
      for(int c=0;c<3;c++)rgb[c]=std::pow(std::max(mixed[c]/std::max(peak,1e-12f),0.f),exponent);
      float newlum=std::max(.2126f*rgb[0]+.7152f*rgb[1]+.0722f*rgb[2],1e-12f);
      for(int c=0;c<3;c++)rgb[c]*=lum/newlum;
    }
  }
  float peak=std::max({rgb[0],rgb[1],rgb[2]}),v=peak;
  if(p.v[1])v*=std::exp2(2*p.v[1]/100*(.18f/(v+.18f)));
  if(p.v[3])v*=1+4*p.v[3]/100*std::exp(-v/.12f);
  if(p.v[4] && p.v[22]<.5f)v=.18f*std::pow(v/.18f,1+.55f*p.v[4]/100);
  float linear[3];for(int c=0;c<3;c++)linear[c]=peak>0?rgb[c]/peak*v:0;
  if(p.v[27]>.5f && ctx.hasDcp && p.v[25]>0){transform(SRGB_TO_PROPHOTO,linear,linear);ctx.dcp.look.apply(linear,p.v[25]/100);transform(PROPHOTO_TO_SRGB,linear,linear);compressGamut(linear);}
  if(int(p.v[22])==3){
    transform(SRGB_TO_PROPHOTO,linear,linear);applyDcpCurve(linear,ctx.dcp.curve);transform(PROPHOTO_TO_SRGB,linear,linear);compressGamut(linear);
  }else viewTransform(linear,int(p.v[22]),p.v[4],p.v[23]/100,p.v[2]);
  float black=p.v[5]/100*.22f;
  float hotspot=1-.55f*p.v[11]/100*std::exp(-((x*x+y*y)/2)/.12f);
  for(int c=0;c<3;c++)a[c]=clamp((encode(std::max(0.f,linear[c]))-black)/(1-black));
  if(profile==8){
    float h,s,val;toHSV(a,h,s,val);
    float gate=smooth((s-.025f)/.16f)*clamp((val-.025f)/.15f);
    float warm=hueWeight(h,12,70)*gate,cool=hueWeight(h,190,85)*gate;
    float shift=std::fmod(354-h+540,360)-180;
    h=std::fmod(h+.72f*warm*shift-12*cool+360,360);
    s=clamp(s*(1+.48f*warm-.3f*cool));
    val*=.88f+.12f*clamp(val/.6f);
    val=clamp(val+.30f*warm*val*(1-val));val*=1-.14f*cool;
    fromHSV(h,s,val,a);
  }
  if(profile==1){
    float h,s,val;toHSV(a,h,s,val);
    float gate=smooth((s-.015f)/.12f)*smooth((val-.02f)/.12f);
    float gw=smooth(hueWeight(h,55,100)/.4f)*gate*p.v[19]/100;
    float bw=smooth(hueWeight(h,220,100)/.4f)*gate*p.v[20]/100;
    float dg=std::fmod(54-h+540,360)-180,db=std::fmod(216-h+540,360)-180;
    h=std::fmod(h+gw*dg+bw*db+360,360);
    s=clamp(s+(.9f*gw+.65f*bw+p.v[21]/100)*s*(1-s));
    fromHSV(h,s,val,a);
  }
  for(int c=0;c<3;c++)a[c]*=hotspot;
  static const int permutations[6][3]={{0,1,2},{2,1,0},{1,0,2},{0,2,1},{2,0,1},{1,2,0}};
  int mode=std::max(0,std::min(5,int(p.v[8])));
  float t=p.v[9]/100,copy[3]={a[0],a[1],a[2]};
  float lum=.2126f*a[0]+.7152f*a[1]+.0722f*a[2];
  t*=1-p.v[16]/100*clamp((lum-.55f)/.35f);
  for(int c=0;c<3;c++)a[c]=copy[c]*(1-t)+copy[permutations[mode][c]]*t;
  if(p.v[10]!=100||p.v[12]||p.v[13]||p.v[29]||p.v[30]||(profile!=1&&(p.v[18]||p.v[19]||p.v[20]||p.v[21]))){
    float mx=std::max({a[0],a[1],a[2]}),mn=std::min({a[0],a[1],a[2]}),d=mx-mn;
    float h=0,s=mx>1e-8?d/mx:0;
    if(d>1e-8){
      if(mx==a[0])h=std::fmod((a[1]-a[2])/d+6,6)*60;
      else if(mx==a[1])h=((a[2]-a[0])/d+2)*60;
      else h=((a[0]-a[1])/d+4)*60;
    }
    float warm=std::max(hueWeight(h,45,75),hueWeight(h,15,45)),cool=hueWeight(h,210,90);
    s=clamp(s*std::max(0.f,1+warm*p.v[29]/100+cool*p.v[30]/100));
    if(profile!=1){s=clamp(s*(1+p.v[18]/100));s=clamp(s+p.v[21]/100*s*(1-s));h=std::fmod(h+30*warm*p.v[19]/100+30*cool*p.v[20]/100+360,360);}
    s=clamp(s*p.v[10]/100);
    mx=clamp(mx*(1+p.v[12]/100*.6f*hueWeight(h,215,70)));
    mx=clamp(mx*(1+p.v[13]/100*.6f*hueWeight(h,95,75)));
    float c=mx*s,z=c*(1-std::fabs(std::fmod(h/60,2)-1)),m=mx-c;
    float values[6][3]={{c,z,0},{z,c,0},{0,c,z},{0,z,c},{z,0,c},{c,0,z}};
    int sector=std::min(5,int(h/60));for(int i=0;i<3;i++)a[i]=values[sector][i]+m;
  }
  if(ctx.cube.size && p.v[26]>0){
    if(p.v[28]>.5f)for(int c=0;c<3;c++)a[c]=a[c]<=.04045f?a[c]/12.92f:std::pow((a[c]+.055f)/1.055f,2.4f);
    ctx.cube.apply(a,p.v[26]/100);
    if(p.v[28]>.5f)for(int c=0;c<3;c++)a[c]=encode(std::max(0.f,a[c]));
  }
}
static void blur(const std::vector<float>& a,std::vector<float>& b,int w,int h){
  b.resize(a.size());
  for(int y=0;y<h;y++)for(int x=0;x<w;x++)for(int c=0;c<3;c++){
    float s=0;for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++)
      s+=a[(size_t(std::max(0,std::min(h-1,y+dy)))*w+std::max(0,std::min(w-1,x+dx)))*3+c]*(dy==0?2:1)*(dx==0?2:1);
    b[(size_t(y)*w+x)*3+c]=s/16;
  }
}
void render(const Image& src,const Params& p,float scale,uint8_t* dest,int stride,const ColorContext* context){
  ColorContext ctx=context?*context:ColorContext();
  if(p.v[27]>.5f&&!ctx.hasDcp)throw std::runtime_error("Import a DCP before enabling it.");
  if(int(p.v[22])==3 && (!ctx.hasDcp||ctx.dcp.curve.size()<4))throw std::runtime_error("The selected DCP has no usable tone curve.");
  float neutral[]={1/std::max(p.v[6],1e-9f),1,1/std::max(p.v[7],1e-9f)};
  ctx.activeInput=p.v[27]>.5f&&ctx.hasDcp?ctx.dcp.inputMatrix(p.v[24]/100,neutral):ctx.cameraToProPhoto;ctx.prepared=true;
  if(ctx.hasDcp && ctx.dcp.base[0].h && ctx.dcp.base[1].h){
    float t=clamp(p.v[24]/100,0,1);
    for(size_t i=0;i<ctx.dcp.base[0].data.size();i++)ctx.dcp.base[0].data[i]=(1-t)*ctx.dcp.base[0].data[i]+t*ctx.dcp.base[1].data[i];
    ctx.dcp.base[1]=HsvTable();
  }
  const bool spatial=p.v[14]>0||p.v[15]>0;
  std::vector<float> image,filtered;
  if(spatial)image.resize(src.pixels.size());
  for(int y=0;y<src.h;y++)for(int x=0;x<src.w;x++){
    float a[3];size_t k=(size_t(y)*src.w+x)*3;
    pixel(&src.pixels[k],p,scale,src.w>1?2.f*x/(src.w-1)-1:0,src.h>1?2.f*y/(src.h-1)-1:0,a,&ctx);
    if(spatial)for(int c=0;c<3;c++)image[k+c]=a[c];
    else{uint8_t* out=dest+size_t(y)*stride+x*4;for(int c=0;c<3;c++)out[c]=uint8_t(clamp(a[c])*255+.5f);out[3]=255;}
  }
  if(!spatial)return;
  // Detail controls operate in display space. Bound blur passes for mobile memory/time.
  if(p.v[14]>0){blur(image,filtered,src.w,src.h);float k=p.v[14]/100*.85f;
    for(size_t i=0;i<image.size();i++)image[i]=image[i]*(1-k)+filtered[i]*k;}
  if(p.v[15]>0){blur(image,filtered,src.w,src.h);float k=p.v[15]/100*1.8f;
    for(size_t i=0;i<image.size();i++)image[i]+=k*(image[i]-filtered[i]);}
  for(int y=0;y<src.h;y++)for(int x=0;x<src.w;x++){
    uint8_t* out=dest+size_t(y)*stride+x*4;size_t k=(size_t(y)*src.w+x)*3;
    for(int c=0;c<3;c++)out[c]=uint8_t(clamp(image[k+c])*255+.5f);out[3]=255;
  }
}
}
