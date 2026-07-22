using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace Trb2xlsx
{
    public class ByteReader : BinaryReader
    {
        public ByteReader(byte[] input) : base(new MemoryStream(input))
        {
        }
        public string ReadString(int Length)
        {
            byte[] Buffer = new byte[Length];
            this.Read(Buffer, 0, Length);
            return Encoding.ASCII.GetString(Buffer);
        }
        public override string ReadString()
        {
            List<byte> buffer = new List<byte>();
            for (;;)
            {
                byte Character = this.ReadByte();
                if (Character == 0) break;
                buffer.Add(Character);
            }
            string Output = Encoding.ASCII.GetString(buffer.ToArray());
            return Output;
        }
        public byte[] ReadBytes()
        {
            List<byte> buffer = new List<byte>();
            for (;;)
            {
                byte Character = this.ReadByte();
                if (Character == 0) break;
                buffer.Add(Character);
            }
            return buffer.ToArray();
        }
        public byte[] ReadBytesFromIndex(int index, int flag)
        {
            this.BaseStream.Seek(index, SeekOrigin.Begin);
            List<byte> buffer = new List<byte>();
            byte[] buffer1 = new byte[1];
            try {

                for (;;)
                {
                    byte Character = this.ReadByte();
                    if (flag != 1)
                    {
                        if (buffer1[0] >= 0x80)
                        {

                            buffer1 = new byte[1];

                        }else
                        if (buffer1[0] == 0 && Character >= 0x80)
                        {
                            buffer1[0] = Character;
                        }

                        else
                        {
                            if (Character == 0) break;
                        }

                    }
                    else {
                        if (Character == 0) break;
                    }
                    buffer.Add(Character);
                }
                return buffer.ToArray();
            }catch(Exception ex)
            {
                throw ex;
            }
        }
        public byte[] ReadBytesFromIndex(int index)
        {
            this.BaseStream.Seek(index, SeekOrigin.Begin);
            List<byte> buffer = new List<byte>();
            for (;;)
            {
                byte Character = this.ReadByte();
                if (Character == 0) break;
                buffer.Add(Character);
            }
            return buffer.ToArray();
        }
        public string ReadStringFromIndex(int index)
        {
            this.BaseStream.Seek(index, SeekOrigin.Begin);
            List<byte> buffer = new List<byte>();
            for (;;)
            {
                byte Character = this.ReadByte();
                if (Character == 0) break;
                buffer.Add(Character);
            }
            string Output = Encoding.ASCII.GetString(buffer.ToArray());
            return Output;
        }
        public string ReadStringFromIndex(int index, int Length)
        {
            this.BaseStream.Seek(index, SeekOrigin.Begin);
            byte[] Buffer = new byte[Length];
            this.Read(Buffer, 0, Length);
            return Encoding.ASCII.GetString(Buffer);
        }

    }
}
