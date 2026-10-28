package cn.infrared.studio;
import android.graphics.Bitmap;
final class Native {
  static { System.loadLibrary("infrared"); }
  static native long open(String path);
  static native void close(long handle);
  static native double[] info(long handle);
  static native String camera(long handle);
  static native void neutral(long handle,float x,float y);
  static native void autoExposure(long handle,float r,float b);
  static native void setScale(long handle,float scale);
  static native Bitmap render(long handle,double[] settings,int full);
}
