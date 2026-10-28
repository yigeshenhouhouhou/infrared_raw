#include "core.h"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <cstdlib>
int main(int argc,char** argv){
  try{
    if(argc>1 && std::string(argv[1])=="--pixels"){
      // Float results for parity tests against the desktop implementation.
      ir::Params p;for(int i=0;i<29 && i+2<argc;i++)p.v[i]=float(std::atof(argv[i+2]));
      std::cout<<std::setprecision(9);
      for(float v:{0.f,.0001f,.01f,.05f,.18f,.5f,1.f,2.f,8.f,100.f}){
        float in[3]={v,v*.6f,v*.2f},out[3];ir::pixel(in,p,1,0,0,out);
        std::cout<<out[0]<<" "<<out[1]<<" "<<out[2]<<"\n";
      }return 0;
    }
    bool full=argc>1 && std::string(argv[1])=="--full";
    bool half=argc>1 && std::string(argv[1])=="--half";
    int shift=full||half?1:0;
    if(argc<3+shift){std::cerr<<"native_check [--full|--half] input.raw output.ppm\n";return 2;}
    auto s=ir::load(argv[1+shift]);ir::Params p;p.v[6]=s.mr;p.v[7]=s.mb;
    if(full||half)s.preview=ir::decode(s.path,half,nullptr,nullptr,nullptr,nullptr,0);
    std::vector<uint8_t> rgba(size_t(s.preview.w)*s.preview.h*4);
    ir::render(s.preview,p,s.scale,rgba.data(),s.preview.w*4,&s.color);
    std::ofstream f(argv[2+shift],std::ios::binary);f<<"P6\n"<<s.preview.w<<" "<<s.preview.h<<"\n255\n";
    for(size_t i=0;i<rgba.size();i+=4)f.write(reinterpret_cast<char*>(&rgba[i]),3);
    std::cout<<s.camera<<" "<<s.width<<"x"<<s.height<<" WB "<<s.mr<<" 1 "<<s.mb<<" scale "<<s.scale<<"\n";
    return 0;
  }catch(const std::exception& ex){std::cerr<<ex.what()<<"\n";return 1;}
}
