package cn.infrared.studio;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Matrix;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.text.InputType;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import java.io.*;
import java.util.ArrayList;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
  private final double[] p={0,0,0,0,0,0,1,1,0,0,100,0,0,0,0,0,0,1,60,85,85,35};
  private final ArrayList<Slider> sliders=new ArrayList<Slider>();
  private final ExecutorService worker=Executors.newSingleThreadExecutor();
  private final Handler ui=new Handler();
  private ImageView image;
  private TextView status,metadata;
  private Button importButton,exportButton,pickButton,wbButton,exposureButton,resetButton;
  private Spinner sizeSpinner,swapSpinner,colorSpinner;
  private Bitmap preview;
  private boolean busy=false,picking=false,syncing=false,destroyed=false;
  private volatile int ticket=0;
  private long handle=0;
  private String sourcePath="",sourceName="";
  private double autoR=1,autoB=1,currentScale=1;
  private int sourceW=0,sourceH=0;
  private SharedPreferences prefs;
  private static final int OPEN=41,SAVE=42;
  private static final int GREEN=0xff176c57,DARK=0xff1c2824;
  private int dp(float n){return (int)(n*getResources().getDisplayMetrics().density+.5f);}
  private LinearLayout vertical(){LinearLayout a=new LinearLayout(this);a.setOrientation(LinearLayout.VERTICAL);return a;}
  private TextView text(String t,int sp){TextView v=new TextView(this);v.setText(t);v.setTextSize(sp);v.setTextColor(DARK);return v;}
  private void heading(LinearLayout parent,String title){TextView v=text(title,15);v.setPadding(0,dp(15),0,dp(5));parent.addView(v);}
  private Button button(LinearLayout parent,String label,View.OnClickListener action){
    Button b=new Button(this);b.setText(label);b.setTextSize(13);b.setAllCaps(false);b.setOnClickListener(action);
    parent.addView(b,new LinearLayout.LayoutParams(0,dp(48),1));return b;
  }
  private LinearLayout row(LinearLayout parent){LinearLayout r=new LinearLayout(this);r.setGravity(Gravity.CENTER_VERTICAL);parent.addView(r);return r;}
  @Override public void onCreate(Bundle state){
    super.onCreate(state);prefs=getSharedPreferences("editing",0);
    ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(0xfff4f5f0);
    LinearLayout root=vertical();root.setPadding(dp(14),dp(14),dp(14),dp(22));scroll.addView(root);setContentView(scroll);
    TextView title=text("红外 RAW",25);root.addView(title);
    root.addView(text("590 nm · 离线解码与浮点调色",13));
    LinearLayout first=row(root);
    importButton=button(first,"导入 RAW",new View.OnClickListener(){public void onClick(View v){chooseRaw();}});
    exportButton=button(first,"导出 JPG",new View.OnClickListener(){public void onClick(View v){chooseSave();}});
    image=new ImageView(this);image.setScaleType(ImageView.ScaleType.FIT_CENTER);image.setBackgroundColor(0xff181d1b);
    image.setContentDescription("照片预览；打开取白后可点击中性区域");
    root.addView(image,new LinearLayout.LayoutParams(-1,dp(280)));
    image.setOnTouchListener(new View.OnTouchListener(){public boolean onTouch(View v,MotionEvent event){
      if(!picking||preview==null||busy)return false;
      if(event.getAction()==MotionEvent.ACTION_UP){
        Matrix inverse=new Matrix();if(!image.getImageMatrix().invert(inverse))return true;
        float[] xy={event.getX()-image.getPaddingLeft(),event.getY()-image.getPaddingTop()};inverse.mapPoints(xy);
        if(xy[0]>=0 && xy[1]>=0 && xy[0]<preview.getWidth() && xy[1]<preview.getHeight())
          sample(xy[0]/preview.getWidth(),xy[1]/preview.getHeight());
      }return true;
    }});
    status=text("从手机或读卡器选择一张 RAW 开始。照片只在本机处理。",13);status.setPadding(0,dp(7),0,dp(7));root.addView(status);
    LinearLayout tools=row(root);
    pickButton=button(tools,"吸管取白",new View.OnClickListener(){public void onClick(View v){picking=!picking;pickButton.setText(picking?"点击中性区域":"吸管取白");}});
    wbButton=button(tools,"自动白平衡",new View.OnClickListener(){public void onClick(View v){p[6]=autoR;p[7]=autoB;sync();schedule();}});
    exposureButton=button(tools,"自动曝光",new View.OnClickListener(){public void onClick(View v){autoExposure();}});
    LinearLayout exportSettings=row(root);exportSettings.addView(text("JPG 尺寸  ",13));
    sizeSpinner=new Spinner(this);sizeSpinner.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,new String[]{"原图尺寸（最多 3600 万像素）","半尺寸（宽高各减半）"}));
    exportSettings.addView(sizeSpinner,new LinearLayout.LayoutParams(0,dp(48),1));
    heading(root,"590 nm 色彩");
    colorSpinner=new Spinner(this);colorSpinner.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,
      new String[]{"原始通道 / 经典互换","590 nm 蓝金（风景假色）"}));root.addView(colorSpinner);colorSpinner.setSelection(1);
    colorSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
      public void onNothingSelected(AdapterView<?> a){}
      public void onItemSelected(AdapterView<?> a,View v,int pos,long id){if(!syncing && p[17]!=pos){p[17]=pos;if(pos==1){p[8]=0;p[9]=0;}sync();schedule();}}
    });
    root.addView(text("蓝金方案已含混色；中性区域请先用吸管校准白平衡。",12));
    addSlider(root,"色彩分离",18,0,100,false);addSlider(root,"暖色金黄",19,0,100,false);
    addSlider(root,"蓝色校正",20,0,100,false);addSlider(root,"自然饱和",21,0,100,false);
    heading(root,"亮度与高光");
    addSlider(root,"曝光 EV",0,-8,8,false);addSlider(root,"中间调",1,-100,100,false);
    addSlider(root,"高光压缩",2,0,100,false);addSlider(root,"阴影",3,0,100,false);
    addSlider(root,"对比度",4,-100,100,false);addSlider(root,"黑场",5,0,100,false);
    heading(root,"白平衡 · G 固定为 1");
    root.addView(text("R / B 可拖动；点击右侧数值可输入倍率。",12));
    addSlider(root,"R",6,-10,6,true);addSlider(root,"B",7,-10,6,true);
    heading(root,"附加通道互换与细节");
    swapSpinner=new Spinner(this);swapSpinner.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,
      new String[]{"不互换","红蓝互换 R ↔ B","红绿互换 R ↔ G","绿蓝互换 G ↔ B","R←B，G←R，B←G","R←G，G←B，B←R"}));root.addView(swapSpinner);
    swapSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){
      public void onNothingSelected(AdapterView<?> a){}
      public void onItemSelected(AdapterView<?> a,View v,int pos,long id){if(!syncing && p[8]!=pos){p[8]=pos;schedule();}}
    });
    addSlider(root,"互换强度",9,0,100,false);addSlider(root,"互换高光保护",16,0,100,false);
    addSlider(root,"饱和度",10,0,200,false);addSlider(root,"冷色明度",12,-100,100,false);addSlider(root,"暖色明度",13,-100,100,false);
    addSlider(root,"中心热点补偿",11,0,100,false);addSlider(root,"降噪",14,0,100,false);addSlider(root,"锐化",15,0,150,false);
    LinearLayout bottom=row(root);
    resetButton=button(bottom,"重置本张调整",new View.OnClickListener(){public void onClick(View v){
      defaults();sync();schedule();
    }});
    button(bottom,"关于 / 开源许可",new View.OnClickListener(){public void onClick(View v){about();}});
    metadata=text("",12);metadata.setPadding(0,dp(12),0,0);root.addView(metadata);
    root.addView(text("相机通道假色 · 传感器已饱和的细节无法恢复\n热点补偿为中心亮度调整，不能准确消除有偏色的反光圆环。",12));
    setBusy(false);
    String saved=prefs.getString("path","");
    if(!saved.isEmpty() && new File(saved).isFile()){
      for(int i=0;i<p.length;i++)p[i]=Double.longBitsToDouble(prefs.getLong("p"+i,Double.doubleToLongBits(p[i])));
      loadFile(saved,prefs.getString("name","RAW"),true);
    }
  }
  private final class Slider {
    int index;double lo,hi;boolean logarithmic;SeekBar bar;TextView label;
    double value(int progress){double x=lo+(hi-lo)*progress/1000.;return logarithmic?Math.pow(2,x):x;}
    void update(){double x=logarithmic?Math.log(Math.max(p[index],.0001))/Math.log(2):p[index];
      bar.setProgress((int)Math.round(1000*(x-lo)/(hi-lo)));
      label.setText(String.format(Locale.US,logarithmic?"%.4f":index==0?"%+.2f":"%.0f",p[index]));}
  }
  private void addSlider(LinearLayout parent,String name,int index,double lo,double hi,boolean log){
    final Slider s=new Slider();s.index=index;s.lo=lo;s.hi=hi;s.logarithmic=log;
    LinearLayout r=row(parent);TextView title=text(name,12);r.addView(title,new LinearLayout.LayoutParams(dp(88),dp(44)));
    s.bar=new SeekBar(this);s.bar.setMax(1000);r.addView(s.bar,new LinearLayout.LayoutParams(0,dp(44),1));
    s.label=text("",12);s.label.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);r.addView(s.label,new LinearLayout.LayoutParams(dp(65),dp(44)));
    s.bar.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener(){
      public void onStartTrackingTouch(SeekBar b){}public void onStopTrackingTouch(SeekBar b){}
      public void onProgressChanged(SeekBar b,int progress,boolean user){if(user&&!syncing){p[s.index]=s.value(progress);s.label.setText(String.format(Locale.US,s.logarithmic?"%.4f":s.index==0?"%+.2f":"%.0f",p[s.index]));schedule();}}
    });
    if(log)s.label.setOnClickListener(new View.OnClickListener(){public void onClick(View v){numeric(s);}});
    sliders.add(s);s.update();
  }
  private void numeric(final Slider s){
    if(busy)return;final EditText input=new EditText(this);input.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_FLAG_DECIMAL);input.setText(String.valueOf(p[s.index]));
    final AlertDialog dialog=new AlertDialog.Builder(this).setTitle("白平衡倍率 0.0001–10000").setView(input).setNegativeButton("取消",null).setPositiveButton("应用",null).create();
    dialog.setOnShowListener(new DialogInterface.OnShowListener(){public void onShow(DialogInterface d){dialog.getButton(-1).setOnClickListener(new View.OnClickListener(){public void onClick(View v){
      try{double n=Double.parseDouble(input.getText().toString());if(Double.isNaN(n)||Double.isInfinite(n)||n<.0001||n>10000)throw new NumberFormatException();p[s.index]=n;sync();schedule();dialog.dismiss();}
      catch(NumberFormatException ex){input.setError("请输入范围内的倍率");}
    }});}});dialog.show();
  }
  private void defaults(){for(int i=0;i<p.length;i++)p[i]=0;p[6]=autoR;p[7]=autoB;p[10]=100;p[17]=1;p[18]=60;p[19]=85;p[20]=85;p[21]=35;}
  private void sync(){syncing=true;for(Slider s:sliders)s.update();if(swapSpinner!=null)swapSpinner.setSelection((int)p[8]);if(colorSpinner!=null)colorSpinner.setSelection((int)p[17]);syncing=false;}
  private void setBusy(boolean value){busy=value;boolean ready=handle!=0 && !value;
    importButton.setEnabled(!value);exportButton.setEnabled(ready);pickButton.setEnabled(ready);wbButton.setEnabled(ready);
    exposureButton.setEnabled(ready);resetButton.setEnabled(ready);sizeSpinner.setEnabled(ready);
    if(swapSpinner!=null)swapSpinner.setEnabled(ready);if(colorSpinner!=null)colorSpinner.setEnabled(ready);for(Slider s:sliders)s.bar.setEnabled(ready);
    if(value)getWindow().addFlags(128);else getWindow().clearFlags(128);
  }
  private String message(Throwable e){String m=e.getMessage();return m==null?e.getClass().getSimpleName():m;}
  private void error(final String prefix,final Throwable e){if(destroyed)return;setBusy(false);status.setText(prefix+"："+message(e));Toast.makeText(this,prefix,Toast.LENGTH_LONG).show();}
  private void chooseRaw(){if(busy)return;Intent i=new Intent("android.intent.action.OPEN_DOCUMENT");i.setType("*/*");i.addCategory(Intent.CATEGORY_OPENABLE);
    try{startActivityForResult(i,OPEN);}catch(Exception e){error("无法打开文件选择器",e);}}
  private void chooseSave(){if(busy||handle==0)return;Intent i=new Intent("android.intent.action.CREATE_DOCUMENT");i.setType("image/jpeg");i.addCategory(Intent.CATEGORY_OPENABLE);
    String base=sourceName.replaceFirst("\\.[^.]+$","");i.putExtra(Intent.EXTRA_TITLE,base+"_infrared.jpg");
    try{startActivityForResult(i,SAVE);}catch(Exception e){error("无法打开保存位置",e);}}
  @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(result!=RESULT_OK||data==null||data.getData()==null)return;
    if(request==OPEN)copyRaw(data.getData());else if(request==SAVE)export(data.getData());}
  private void copyRaw(final Uri uri){
    if(busy)return;setBusy(true);status.setText("正在读取 RAW…");
    worker.execute(new Runnable(){public void run(){
      String name="photo.raw";File dest=new File(getFilesDir(),"raw_"+System.currentTimeMillis()+".raw");
      try{
        Cursor c=getContentResolver().query(uri,new String[]{"_display_name"},null,null,null);if(c!=null){try{if(c.moveToFirst())name=c.getString(0);}finally{c.close();}}
        InputStream in=getContentResolver().openInputStream(uri);if(in==null)throw new IOException("文件不可读");
        try{OutputStream out=new FileOutputStream(dest);try{byte[] b=new byte[262144];long total=0;int n;
          while((n=in.read(b))!=-1){total+=n;if(total>512L*1024*1024)throw new IOException("RAW 超过 512 MB");out.write(b,0,n);}
          if(total==0)throw new IOException("文件为空");}finally{out.close();}}
        finally{in.close();}
        final String chosen=name;ui.post(new Runnable(){public void run(){if(!destroyed)loadFile(dest.getAbsolutePath(),chosen,false);}});
      }catch(final Throwable e){dest.delete();ui.post(new Runnable(){public void run(){error("读取失败",e);}});}
    }});
  }
  private void loadFile(final String path,final String name,final boolean restore){
    setBusy(true);ticket++;status.setText("正在解码 RAW；首次处理可能需要几秒…");
    worker.execute(new Runnable(){public void run(){long next=0;
      try{
        next=Native.open(path);final double[] info=Native.info(next);final String camera=Native.camera(next);
        final boolean migrate=restore && prefs.getInt("engine",1)<2;
        if(restore && !migrate)Native.setScale(next,(float)Double.longBitsToDouble(prefs.getLong("scale",Double.doubleToLongBits(info[4]))));
        if(handle!=0)Native.close(handle);handle=next;next=0;
        final String old=sourcePath;sourcePath=path;sourceName=name;
        if(!old.isEmpty()&&!old.equals(path))new File(old).delete();
        ui.post(new Runnable(){public void run(){if(destroyed)return;
          sourceW=(int)info[0];sourceH=(int)info[1];autoR=info[2];autoB=info[3];
          if(!restore)defaults();
          if(migrate){p[8]=0;p[9]=0;p[17]=1;p[18]=60;p[19]=85;p[20]=85;p[21]=35;
            Toast.makeText(MainActivity.this,"已启用新版蓝金混色与自动曝光；旧通道互换已归零",Toast.LENGTH_LONG).show();}
          currentScale=restore && !migrate?Double.longBitsToDouble(prefs.getLong("scale",Double.doubleToLongBits(info[4]))):info[4];
          metadata.setText(name+"\n"+camera+" · "+sourceW+" × "+sourceH+String.format(Locale.US,"\n传感器饱和采样：%.3f%%",info[5]));
          if((long)sourceW*sourceH>36000000)sizeSpinner.setSelection(1);
          setBusy(false);sync();schedule();persist();
        }});
      }catch(final Throwable e){if(next!=0)Native.close(next);if(!restore)new File(path).delete();ui.post(new Runnable(){public void run(){error("RAW 解码失败",e);}});}
    }});
  }
  private final Runnable delayed=new Runnable(){public void run(){renderPreview();}};
  private void schedule(){if(handle==0||busy||destroyed)return;ticket++;ui.removeCallbacks(delayed);ui.postDelayed(delayed,140);}
  private void renderPreview(){if(handle==0||busy||destroyed)return;final int version=ticket;final double[] values=p.clone();status.setText("更新预览…");
    worker.execute(new Runnable(){public void run(){if(version!=ticket||destroyed)return;
      try{final Bitmap result=Native.render(handle,values,0);ui.post(new Runnable(){public void run(){
        if(destroyed||version!=ticket){result.recycle();return;}Bitmap old=preview;preview=result;image.setImageBitmap(result);if(old!=null)old.recycle();status.setText(picking?"点击画面中应为中性的区域取白":"预览已更新 · 可继续调整或导出 JPG");persist();
      }});}catch(final Throwable e){ui.post(new Runnable(){public void run(){if(version==ticket)error("预览失败",e);}});}
    }});
  }
  private void sample(final float x,final float y){if(handle==0||busy)return;setBusy(true);ticket++;
    worker.execute(new Runnable(){public void run(){try{Native.neutral(handle,x,y);final double[] info=Native.info(handle);ui.post(new Runnable(){public void run(){if(destroyed)return;p[6]=info[2];p[7]=info[3];picking=false;pickButton.setText("吸管取白");setBusy(false);sync();schedule();}});}
      catch(final Throwable e){ui.post(new Runnable(){public void run(){error("取白失败",e);}});}}});
  }
  private void autoExposure(){if(handle==0||busy)return;setBusy(true);ticket++;final float r=(float)p[6],b=(float)p[7];
    worker.execute(new Runnable(){public void run(){try{Native.autoExposure(handle,r,b);final double scale=Native.info(handle)[4];ui.post(new Runnable(){public void run(){if(destroyed)return;currentScale=scale;p[0]=0;setBusy(false);sync();schedule();}});}
      catch(final Throwable e){ui.post(new Runnable(){public void run(){error("自动曝光失败",e);}});}}});
  }
  private byte[] asset(String name)throws IOException{InputStream in=getAssets().open(name);try{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);return out.toByteArray();}finally{in.close();}}
  private void export(final Uri uri){if(busy||handle==0)return;setBusy(true);ticket++;final double[] values=p.clone();final int mode=sizeSpinner.getSelectedItemPosition()==0?1:2;status.setText("正在生成 JPG，请保持应用打开…");
    worker.execute(new Runnable(){public void run(){Bitmap bitmap=null;
      try{
        bitmap=Native.render(handle,values,mode);final int w=bitmap.getWidth(),h=bitmap.getHeight();
        ByteArrayOutputStream buffer=new ByteArrayOutputStream();if(!bitmap.compress(Bitmap.CompressFormat.JPEG,95,buffer))throw new IOException("JPG 编码失败");
        bitmap.recycle();bitmap=null;byte[] jpg=buffer.toByteArray(),icc=asset("sRGB.icc");
        OutputStream out=getContentResolver().openOutputStream(uri,"w");if(out==null)throw new IOException("保存位置不可写");
        try{out.write(jpg,0,2);out.write(255);out.write(226);int length=icc.length+16;out.write(length>>8);out.write(length&255);
          out.write(new byte[]{73,67,67,95,80,82,79,70,73,76,69,0,1,1});out.write(icc);out.write(jpg,2,jpg.length-2);out.flush();}finally{out.close();}
        ui.post(new Runnable(){public void run(){if(destroyed)return;setBusy(false);status.setText("JPG 已保存 · "+w+" × "+h+" · sRGB");Toast.makeText(MainActivity.this,"JPG 已保存到所选位置",Toast.LENGTH_LONG).show();}});
      }catch(final Throwable e){if(bitmap!=null)bitmap.recycle();try{getContentResolver().delete(uri,null,null);}catch(Exception ignored){}
        ui.post(new Runnable(){public void run(){error("导出失败",e);}});}
    }});
  }
  private void persist(){if(handle==0)return;SharedPreferences.Editor e=prefs.edit();e.putInt("engine",2);e.putString("path",sourcePath);e.putString("name",sourceName);e.putLong("scale",Double.doubleToLongBits(currentScale));for(int i=0;i<p.length;i++)e.putLong("p"+i,Double.doubleToLongBits(p[i]));e.apply();}
  private void about(){try{new AlertDialog.Builder(this).setTitle("红外 RAW 0.2.0").setMessage(new String(asset("NOTICE.txt"),"UTF-8")).setPositiveButton("关闭",null).show();}catch(Exception e){error("无法打开说明",e);}}
  @Override protected void onPause(){super.onPause();persist();}
  @Override protected void onDestroy(){destroyed=true;ticket++;ui.removeCallbacks(delayed);worker.execute(new Runnable(){public void run(){if(handle!=0){Native.close(handle);handle=0;}}});worker.shutdown();if(preview!=null){image.setImageBitmap(null);preview.recycle();preview=null;}super.onDestroy();}
}
